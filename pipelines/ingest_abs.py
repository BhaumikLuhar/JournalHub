from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ingestion.abs.parser import read_abs_csv
from ingestion.abs.transformer import transform_abs_row
from ingestion.abs.validator import validate_record
from ingestion.common.csv_loader import (
    compute_sha256,
    serialize_json_row,
)
from ingestion.common.pipeline_helpers import (
    get_or_create_dataset,
    run_dataset_import,
)
from ingestion.common.validation import log_rejected_row


logger = logging.getLogger(__name__)


BASE_DIR = Path("data/raw/abs")

ABS_FILE_NAME = "abs_ajg_2024.csv"

PARSER_VERSION = "1.0"


def _get_abs_file() -> Path:
    """
    Return the expected ABS CSV file.

    The raw directory is treated as read-only.
    """

    path = BASE_DIR / ABS_FILE_NAME

    if not path.exists():
        raise FileNotFoundError(
            f"Expected ABS CSV file does not exist: {path}"
        )

    if not path.is_file():
        raise ValueError(
            f"Expected ABS CSV path is not a file: {path}"
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
    Insert one raw-file lineage record.
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
    Store exactly one original wide ABS source row.
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


def _insert_abs_record(
    cursor: Any,
    *,
    dataset_id: int,
    raw_row_id: int,
    record: dict[str, Any],
    raw_row: dict[str, Any],
) -> int:
    """
    Insert one normalized ABS rating-year record.

    journal_id remains NULL until entity resolution.
    """

    cursor.execute(
        """
        INSERT INTO abs_records (
            journal_id,
            dataset_id,
            raw_row_id,
            source_row_hash,
            rating_year,
            journal_name,
            field,
            issn,
            publisher,
            rating,
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
            record["rating_year"],
            record["journal_name"],
            record["field"],
            record["issn"],
            record["publisher"],
            record["rating"],
            json.dumps(
                serialize_json_row(raw_row),
                ensure_ascii=True,
                allow_nan=False,
            ),
        ),
    )

    return int(cursor.fetchone()[0])


def _import_abs(
    cursor: Any,
    *,
    dataset_id: int,
    file_path: Path,
    file_hash: str,
) -> int:
    """
    Import the complete ABS CSV inside the transaction supplied by
    run_dataset_import().

    Lineage model:

        one original wide source row
            -> one raw_rows row
            -> zero or more abs_records rows
    """

    raw_file_id = _insert_raw_file(
        cursor,
        dataset_id=dataset_id,
        file_path=file_path,
        file_hash=file_hash,
    )

    dataframe = read_abs_csv(file_path)

    imported_count = 0
    rejected_count = 0
    duplicate_count = 0

    # Database uniqueness:
    #
    #   (dataset_id, source_row_hash, rating_year)
    #
    # This in-memory guard makes duplicate detection explicit before
    # PostgreSQL is asked to enforce the constraint.
    seen_records: set[tuple[int, str, int]] = set()

    for row_number, (_, row) in enumerate(
        dataframe.iterrows(),
        start=1,
    ):
        # ---------------------------------------------------------
        # Preserve the ORIGINAL wide source row first.
        # ---------------------------------------------------------
        raw_row = row.to_dict()

        raw_row_id = _insert_raw_row(
            cursor,
            raw_file_id=raw_file_id,
            row_number=row_number,
            raw_row=raw_row,
        )

        # ---------------------------------------------------------
        # Transform this exact source row.
        #
        # The transformer computes the source-row hash once and
        # reuses it for every rating-year record emitted from this
        # row.
        # ---------------------------------------------------------
        records = transform_abs_row(
            raw_row
        )

        # A source row with no populated rating cells is legitimate
        # as a raw source row but produces no normalized records.
        for record in records:
            duplicate_key = (
                dataset_id,
                record["source_row_hash"],
                record["rating_year"],
            )

            if duplicate_key in seen_records:
                duplicate_count += 1

                logger.warning(
                    "Skipping duplicate ABS normalized record: "
                    "dataset_id=%s, source_row_hash=%s, "
                    "rating_year=%s, raw_row_number=%s",
                    dataset_id,
                    record["source_row_hash"],
                    record["rating_year"],
                    row_number,
                )

                continue

            seen_records.add(
                duplicate_key
            )

            # -----------------------------------------------------
            # Validate normalized record.
            # -----------------------------------------------------
            problems = validate_record(
                record
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

            # -----------------------------------------------------
            # Insert normalized ABS record.
            #
            # raw_row_id points to the ONE raw row corresponding
            # to this original wide source row.
            # -----------------------------------------------------
            _insert_abs_record(
                cursor,
                dataset_id=dataset_id,
                raw_row_id=raw_row_id,
                record=record,
                raw_row=raw_row,
            )

            imported_count += 1

    logger.info(
        "ABS import complete: "
        "raw_rows=%d, imported=%d, rejected=%d, duplicates=%d",
        len(dataframe),
        imported_count,
        rejected_count,
        duplicate_count,
    )

    return imported_count


def ingest_abs() -> None:
    """
    Ingest the verified ABS AJG 2024 CSV.
    """

    if not BASE_DIR.exists():
        raise FileNotFoundError(
            f"ABS raw directory does not exist: {BASE_DIR}"
        )

    file_path = _get_abs_file()

    file_hash = compute_sha256(
        file_path
    )

    logger.info(
        "ABS file: %s",
        file_path,
    )

    logger.info(
        "ABS SHA-256: %s",
        file_hash,
    )

    dataset = get_or_create_dataset(
        source_code="ABS",
        dataset_year=2024,
        subject_area=None,
        file_name=file_path.name,
        file_hash=file_hash,
    )

    dataset_id = dataset["dataset_id"]
    action = dataset["action"]

    if action == "skip":
        logger.info(
            "Skipping already-loaded ABS file: %s "
            "(dataset_id=%s)",
            file_path.name,
            dataset_id,
        )
        return

    logger.info(
        "Importing ABS file: %s "
        "(dataset_id=%s, action=%s)",
        file_path.name,
        dataset_id,
        action,
    )

    def import_file(cursor: Any) -> int:
        return _import_abs(
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
        "Loaded ABS file successfully: %s "
        "(dataset_id=%s)",
        file_path.name,
        dataset_id,
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    ingest_abs()


if __name__ == "__main__":
    main()