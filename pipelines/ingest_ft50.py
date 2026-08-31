from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ingestion.common.csv_loader import (
    compute_sha256,
    serialize_json_row,
)
from ingestion.common.pipeline_helpers import (
    get_or_create_dataset,
    run_dataset_import,
)
from ingestion.common.validation import log_rejected_row
from ingestion.ft50.parser import read_ft50_csv
from ingestion.ft50.transformer import transform_ft50_row


logger = logging.getLogger(__name__)


BASE_DIR = Path("data/raw/ft50")

FT50_FILE_NAME = "ft50.csv"

PARSER_VERSION = "1.0"

SOURCE_CODE = "FT50"

DATASET_YEAR = 2026

TABLE_NAME = "ft50_records"


def _get_ft50_file() -> Path:
    """
    Return the expected FT50 CSV file.

    The raw directory is intentionally treated as read-only.
    """

    path = BASE_DIR / FT50_FILE_NAME

    if not path.exists():
        raise FileNotFoundError(
            f"Expected FT50 CSV file does not exist: {path}"
        )

    if not path.is_file():
        raise ValueError(
            f"Expected FT50 CSV path is not a file: {path}"
        )

    return path


def _insert_raw_file(
    cursor: Any,
    *,
    dataset_id: int,
    file_path: Path,
    file_hash: str,
) -> int:
    """
    Insert one raw-file lineage record and return its ID.
    """

    cursor.execute(
        """
        INSERT INTO raw_files (
            dataset_id,
            file_path,
            sha256,
            file_size,
            parser_version
        )
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            dataset_id,
            str(file_path),
            file_hash,
            file_path.stat().st_size,
            PARSER_VERSION,
        ),
    )

    return int(cursor.fetchone()[0])


def _insert_raw_row(
    cursor: Any,
    *,
    raw_file_id: int,
    row_number: int,
    raw_row: dict[str, Any],
) -> int:
    """
    Store exactly one original FT50 source row in raw_rows.

    No domain normalization is performed here.
    """

    cursor.execute(
        """
        INSERT INTO raw_rows (
            raw_file_id,
            row_number,
            raw_data
        )
        VALUES (%s, %s, %s::jsonb)
        RETURNING id
        """,
        (
            raw_file_id,
            row_number,
            json.dumps(
                serialize_json_row(raw_row),
                ensure_ascii=True,
                allow_nan=False,
            ),
        ),
    )

    return int(cursor.fetchone()[0])


def _insert_ft50_record(
    cursor: Any,
    *,
    dataset_id: int,
    raw_row_id: int,
    record: dict[str, Any],
    raw_row: dict[str, Any],
) -> int:
    """
    Insert one normalized FT50 record.

    journal_id intentionally remains NULL during ingestion.

    Entity resolution is performed separately by resolve_ft50.py.
    """

    cursor.execute(
        """
        INSERT INTO ft50_records (
            journal_id,
            dataset_id,
            raw_row_id,
            source_row_hash,
            ft50_year,
            rank,
            journal_name,
            included,
            raw_json
        )
        VALUES (
            NULL,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s::jsonb
        )
        RETURNING id
        """,
        (
            dataset_id,
            raw_row_id,
            record["source_row_hash"],
            record["ft50_year"],
            record["rank"],
            record["journal_name"],
            record["included"],
            json.dumps(
                serialize_json_row(raw_row),
                ensure_ascii=True,
                allow_nan=False,
            ),
        ),
    )

    return int(cursor.fetchone()[0])


def _import_ft50(
    cursor: Any,
    *,
    dataset_id: int,
    file_path: Path,
    file_hash: str,
) -> int:
    """
    Import the complete FT50 CSV inside the transaction supplied by
    run_dataset_import().

    Lineage model:

        one original FT50 source row
            -> one raw_rows row
            -> one ft50_records row

    Returns:
        Number of successfully inserted ft50_records.
    """

    raw_file_id = _insert_raw_file(
        cursor,
        dataset_id=dataset_id,
        file_path=file_path,
        file_hash=file_hash,
    )

    dataframe = read_ft50_csv(
        file_path
    )

    imported_count = 0
    rejected_count = 0
    duplicate_count = 0

    # The database uniqueness key is:
    #
    #     (dataset_id, source_row_hash, ft50_year)
    #
    # This in-memory set provides the explicit in-file duplicate guard.
    seen_records: set[tuple[int, str, int]] = set()

    for row_number, (_, row) in enumerate(
        dataframe.iterrows(),
        start=1,
    ):
        # ---------------------------------------------------------
        # Preserve the original source row first.
        # ---------------------------------------------------------

        raw_row = row.to_dict()

        raw_row_id = _insert_raw_row(
            cursor,
            raw_file_id=raw_file_id,
            row_number=row_number,
            raw_row=raw_row,
        )

        # ---------------------------------------------------------
        # Transform exactly this original source row.
        # ---------------------------------------------------------

        record = transform_ft50_row(
            raw_row
        )

        duplicate_key = (
            dataset_id,
            record["source_row_hash"],
            record["ft50_year"],
        )

        if duplicate_key in seen_records:
            duplicate_count += 1

            logger.warning(
                "Skipping duplicate FT50 source row: "
                "dataset_id=%s, source_row_hash=%s, "
                "ft50_year=%s, row_number=%s",
                dataset_id,
                record["source_row_hash"],
                record["ft50_year"],
                row_number,
            )

            continue

        seen_records.add(
            duplicate_key
        )

        # ---------------------------------------------------------
        # Structural validation.
        #
        # The FT50 schema requires:
        #   - journal_name
        #   - ft50_year
        #   - source_row_hash
        #
        # rank is allowed to be NULL at the database level.
        # included is always True by source semantics.
        # ---------------------------------------------------------

        problems: list[str] = []

        if not record["journal_name"]:
            problems.append(
                "missing journal_name"
            )

        if record["ft50_year"] is None:
            problems.append(
                "missing or invalid ft50_year"
            )

        if not record["source_row_hash"]:
            problems.append(
                "missing source_row_hash"
            )

        if record["included"] is not True:
            problems.append(
                "included must be True for FT50"
            )

        if problems:
            for problem in problems:
                log_rejected_row(
                    cursor=cursor,
                    dataset_id=dataset_id,
                    raw_file_id=raw_file_id,
                    row_number=row_number,
                    reason=problem,
                    raw_row=raw_row,
                )

            rejected_count += 1
            continue

        # ---------------------------------------------------------
        # Insert normalized FT50 record.
        #
        # journal_id deliberately remains NULL.
        # ---------------------------------------------------------

        _insert_ft50_record(
            cursor,
            dataset_id=dataset_id,
            raw_row_id=raw_row_id,
            record=record,
            raw_row=raw_row,
        )

        imported_count += 1

    logger.info(
        "FT50 import complete: "
        "raw_rows=%d, imported=%d, rejected=%d, duplicates=%d",
        len(dataframe),
        imported_count,
        rejected_count,
        duplicate_count,
    )

    return imported_count


def ingest_ft50() -> None:
    """
    Ingest the verified FT50 CSV.
    """

    if not BASE_DIR.exists():
        raise FileNotFoundError(
            f"FT50 raw directory does not exist: {BASE_DIR}"
        )

    file_path = _get_ft50_file()

    file_hash = compute_sha256(
        file_path
    )

    logger.info(
        "FT50 file: %s",
        file_path,
    )

    logger.info(
        "FT50 SHA-256: %s",
        file_hash,
    )

    dataset = get_or_create_dataset(
        source_code=SOURCE_CODE,
        dataset_year=DATASET_YEAR,
        subject_area=None,
        file_name=file_path.name,
        file_hash=file_hash,
    )

    dataset_id = dataset["dataset_id"]
    action = dataset["action"]

    if action == "skip":
        logger.info(
            "Skipping already-loaded FT50 file: %s "
            "(dataset_id=%s)",
            file_path.name,
            dataset_id,
        )
        return

    logger.info(
        "Importing FT50 file: %s "
        "(dataset_id=%s, action=%s)",
        file_path.name,
        dataset_id,
        action,
    )

    def import_file(cursor: Any) -> int:
        return _import_ft50(
            cursor,
            dataset_id=dataset_id,
            file_path=file_path,
            file_hash=file_hash,
        )

    run_dataset_import(
        dataset_id,
        import_file,
    )

    logger.info(
        "Loaded FT50 file successfully: %s "
        "(dataset_id=%s)",
        file_path.name,
        dataset_id,
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    ingest_ft50()


if __name__ == "__main__":
    main()