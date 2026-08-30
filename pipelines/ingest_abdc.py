from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ingestion.abdc.parser import (
    list_supported_sheets,
    read_abdc_sheet,
)
from ingestion.abdc.transformer import transform_row
from ingestion.abdc.validator import validate_record
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


BASE_DIR = Path("data/raw/abdc")

ABDC_FILE_NAME = "ABDC-JQL-2025-v1-260326.xlsx"

PARSER_VERSION = "1.0"


def _get_abdc_file() -> Path:
    """
    Return the expected ABDC workbook.

    The raw directory is intentionally treated as read-only. The pipeline
    discovers the verified workbook without modifying it.
    """

    path = BASE_DIR / ABDC_FILE_NAME

    if not path.exists():
        raise FileNotFoundError(
            f"Expected ABDC workbook does not exist: {path}"
        )

    if not path.is_file():
        raise ValueError(
            f"Expected ABDC workbook path is not a file: {path}"
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
    Insert one raw-file lineage row and return its ID.
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

    return cursor.fetchone()[0]


def _insert_raw_row(
    cursor: Any,
    *,
    raw_file_id: int,
    row_number: int,
    raw_row: dict[str, Any],
) -> int:
    """
    Store one source row snapshot in raw_rows.

    The row is serialized only for JSON compatibility. No domain
    normalization is performed here.
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

    return cursor.fetchone()[0]


def _insert_abdc_record(
    cursor: Any,
    *,
    dataset_id: int,
    raw_row_id: int,
    record: dict[str, Any],
    raw_row: dict[str, Any],
) -> int:
    """
    Insert one normalized ABDC record.

    journal_id intentionally remains NULL during ingestion. Entity
    resolution assigns it later.
    """

    cursor.execute(
        """
        INSERT INTO abdc_records (
            journal_id,
            dataset_id,
            raw_row_id,
            source_row_hash,
            rating_year,
            journal_name,
            publisher,
            issn,
            issn_online,
            year_inception,
            for_code,
            for_scheme,
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
            record["publisher"],
            record["issn"],
            record["issn_online"],
            record["year_inception"],
            record["for_code"],
            record["for_scheme"],
            record["rating"],
            json.dumps(
                serialize_json_row(raw_row),
                ensure_ascii=True,
                allow_nan=False,
            ),
        ),
    )

    return cursor.fetchone()[0]


def _import_workbook(
    cursor: Any,
    *,
    dataset_id: int,
    file_path: Path,
    file_hash: str,
) -> int:
    """
    Import the complete ABDC workbook inside the transaction supplied by
    run_dataset_import().

    One raw_file represents the physical XLSX workbook. Every source row
    from every worksheet receives its own raw_rows entry.

    Returns:
        Number of successfully inserted abdc_records.
    """

    raw_file_id = _insert_raw_file(
        cursor,
        dataset_id=dataset_id,
        file_path=file_path,
        file_hash=file_hash,
    )

    imported_count = 0

    # The database uniqueness key is:
    #
    #     (dataset_id, source_row_hash, rating_year)
    #
    # The database already protects us against committed duplicates, but
    # this in-memory set provides the explicit Day-6 in-file duplicate guard.
    seen_records: set[tuple[int, str, int]] = set()

    # raw_rows.row_number is scoped to raw_file_id, not to a worksheet.
    # Therefore assign monotonically increasing source-row numbers across
    # the entire workbook rather than restarting at 1 for each sheet.
    raw_row_number = 0

    for sheet_name in list_supported_sheets():
        logger.info(
            "Reading ABDC sheet: %s",
            sheet_name,
        )

        dataframe = read_abdc_sheet(
            file_path,
            sheet_name,
        )

        sheet_imported = 0
        sheet_rejected = 0
        sheet_duplicates = 0

        for _, row in dataframe.iterrows():
            raw_row_number += 1

            raw_row = row.to_dict()

            # ---------------------------------------------------------
            # Raw source snapshot
            # ---------------------------------------------------------
            #
            # This happens before transformation/validation.
            # ---------------------------------------------------------
            raw_row_id = _insert_raw_row(
                cursor,
                raw_file_id=raw_file_id,
                row_number=raw_row_number,
                raw_row=raw_row,
            )

            # ---------------------------------------------------------
            # Transform
            # ---------------------------------------------------------
            record = transform_row(
                raw_row,
                sheet_name=sheet_name,
            )

            duplicate_key = (
                dataset_id,
                record["source_row_hash"],
                record["rating_year"],
            )

            if duplicate_key in seen_records:
                logger.warning(
                    "Skipping duplicate ABDC source row: "
                    "dataset_id=%s, sheet=%s, "
                    "source_row_hash=%s, rating_year=%s, "
                    "row_number=%s",
                    dataset_id,
                    sheet_name,
                    record["source_row_hash"],
                    record["rating_year"],
                    raw_row_number,
                )

                sheet_duplicates += 1
                continue

            seen_records.add(duplicate_key)

            # ---------------------------------------------------------
            # Validate normalized record
            # ---------------------------------------------------------
            problems = validate_record(record)

            if problems:
                for problem in problems:
                    log_rejected_row(
                        cursor=cursor,
                        dataset_id=dataset_id,
                        raw_file_id=raw_file_id,
                        row_number=raw_row_number,
                        reason=problem,
                        raw_row=raw_row,
                    )

                sheet_rejected += 1
                continue

            # ---------------------------------------------------------
            # Insert normalized ABDC record
            # ---------------------------------------------------------
            _insert_abdc_record(
                cursor,
                dataset_id=dataset_id,
                raw_row_id=raw_row_id,
                record=record,
                raw_row=raw_row,
            )

            imported_count += 1
            sheet_imported += 1

        logger.info(
            "ABDC sheet complete: %s "
            "(rows=%d, imported=%d, rejected=%d, duplicates=%d)",
            sheet_name,
            len(dataframe),
            sheet_imported,
            sheet_rejected,
            sheet_duplicates,
        )

    return imported_count


def ingest_abdc() -> None:
    """
    Ingest the verified ABDC workbook.

    The workbook contains historical rating sheets for 2010, 2013, 2016,
    2019, 2022, and 2025. They are loaded as one physical source artifact
    and distinguished in abdc_records by rating_year.
    """

    if not BASE_DIR.exists():
        raise FileNotFoundError(
            f"ABDC raw directory does not exist: {BASE_DIR}"
        )

    file_path = _get_abdc_file()

    file_hash = compute_sha256(file_path)

    logger.info(
        "ABDC workbook: %s",
        file_path,
    )

    logger.info(
        "ABDC SHA-256: %s",
        file_hash,
    )

    dataset = get_or_create_dataset(
        source_code="ABDC",
        dataset_year=2025,
        subject_area=None,
        file_name=file_path.name,
        file_hash=file_hash,
    )

    dataset_id = dataset["dataset_id"]
    action = dataset["action"]

    if action == "skip":
        logger.info(
            "Skipping already-loaded ABDC workbook: %s "
            "(dataset_id=%s)",
            file_path.name,
            dataset_id,
        )
        return

    logger.info(
        "Importing ABDC workbook: %s "
        "(dataset_id=%s, action=%s)",
        file_path.name,
        dataset_id,
        action,
    )

    def import_file(cursor: Any) -> int:
        return _import_workbook(
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
        "Loaded ABDC workbook successfully: %s "
        "(dataset_id=%s)",
        file_path.name,
        dataset_id,
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    ingest_abdc()


if __name__ == "__main__":
    main()