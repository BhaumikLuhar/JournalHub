"""SCImago raw + normalized ingestion pipeline for JournalHub."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ingestion.common.csv_loader import (
    compute_sha256,
    load_csv,
    serialize_json_row,
)
from ingestion.common.pipeline_helpers import (
    get_or_create_dataset,
    run_dataset_import,
)
from ingestion.common.validation import log_rejected_row
from ingestion.scimago.parser import parse_filename
from ingestion.scimago.transformer import transform_row
from ingestion.scimago.validator import validate_row

logger = logging.getLogger(__name__)

BASE_DIR = Path("data/raw/scimago")
PARSER_VERSION = "1.0"


def _insert_raw_file(
    cursor: Any,
    *,
    dataset_id: int,
    file_path: Path,
    file_hash: str,
) -> int:
    """Insert one raw file metadata row and return its ID."""

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
    """Insert the source row as a valid JSON representation."""
    import json

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


def _insert_scimago_record(
    cursor: Any,
    *,
    dataset_id: int,
    raw_row_id: int,
    record: dict[str, Any],
    raw_row: dict[str, Any],
) -> int:
    """Insert one normalized SCImago record."""

    import json

    cursor.execute(
        """
        INSERT INTO scimago_records (
            journal_id,
            dataset_id,
            raw_row_id,
            year,
            subject_area,
            rank,
            sourceid,
            title,
            type,
            issn_raw,
            publisher_raw,
            open_access,
            open_access_diamond,
            sjr,
            sjr_best_quartile,
            h_index,
            total_docs,
            total_docs_3years,
            total_refs,
            total_citations_3years,
            citable_docs_3years,
            citations_per_doc_2years,
            refs_per_doc,
            female_percentage,
            overton,
            country,
            region,
            coverage,
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
            record["year"],
            record["subject_area"],
            record["rank"],
            record["sourceid"],
            record["title"],
            record["type"],
            record["issn_raw"],
            record["publisher_raw"],
            record["open_access"],
            record["open_access_diamond"],
            record["sjr"],
            record["sjr_best_quartile"],
            record["h_index"],
            record["total_docs"],
            record["total_docs_3years"],
            record["total_refs"],
            record["total_citations_3years"],
            record["citable_docs_3years"],
            record["citations_per_doc_2years"],
            record["refs_per_doc"],
            record["female_percentage"],
            record["overton"],
            record["country"],
            record["region"],
            record["coverage"],
            json.dumps(
                serialize_json_row(raw_row),
                ensure_ascii=True,
                allow_nan=False,
            ),
        ),
    )

    return cursor.fetchone()[0]


def _insert_categories(
    cursor: Any,
    *,
    scimago_record_id: int,
    categories: list[dict[str, str | None]],
) -> None:
    """Insert normalized SCImago category rows."""

    for category in categories:
        cursor.execute(
            """
            INSERT INTO scimago_categories (
                scimago_record_id,
                category_name,
                quartile
            )
            VALUES (%s, %s, %s)
            """,
            (
                scimago_record_id,
                category["category_name"],
                category["quartile"],
            ),
        )


def _insert_areas(
    cursor: Any,
    *,
    scimago_record_id: int,
    areas: list[str],
) -> None:
    """Insert normalized SCImago area rows."""

    for area in areas:
        cursor.execute(
            """
            INSERT INTO scimago_areas (
                scimago_record_id,
                area_name
            )
            VALUES (%s, %s)
            """,
            (
                scimago_record_id,
                area,
            ),
        )


def _import_file(
    cursor: Any,
    *,
    dataset_id: int,
    file_path: Path,
    file_hash: str,
    year: int,
    subject_area: str,
) -> int:
    """Import one SCImago file inside the supplied transaction."""

    raw_file_id = _insert_raw_file(
        cursor,
        dataset_id=dataset_id,
        file_path=file_path,
        file_hash=file_hash,
    )

    frame = load_csv(
        file_path,
        delimiter=";",
    )

    imported_count = 0

    for row_number, (_, row) in enumerate(
        frame.iterrows(),
        start=1,
    ):
        raw_row = row.to_dict()

        raw_row_id = _insert_raw_row(
            cursor,
            raw_file_id=raw_file_id,
            row_number=row_number,
            raw_row=raw_row,
        )

        record = transform_row(
            raw_row,
            year=year,
            subject_area=subject_area,
        )

        if record["sjr_out_of_range"]:
            log_rejected_row(
                cursor=cursor,
                dataset_id=dataset_id,
                raw_file_id=raw_file_id,
                row_number=row_number,
                reason="sjr_out_of_range",
                raw_row=raw_row,
            )
            continue

        problems = validate_row(record)

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
            continue

        scimago_record_id = _insert_scimago_record(
            cursor,
            dataset_id=dataset_id,
            raw_row_id=raw_row_id,
            record=record,
            raw_row=raw_row,
        )

        _insert_categories(
            cursor,
            scimago_record_id=scimago_record_id,
            categories=record["categories"],
        )

        _insert_areas(
            cursor,
            scimago_record_id=scimago_record_id,
            areas=record["areas"],
        )

        imported_count += 1

    return imported_count


def ingest_scimago() -> None:
    """Discover and ingest every SCImago CSV currently present."""

    if not BASE_DIR.exists():
        raise FileNotFoundError(
            f"SCImago raw directory does not exist: {BASE_DIR}"
        )

    files = sorted(BASE_DIR.rglob("*.csv"))

    if not files:
        raise FileNotFoundError(
            f"No SCImago CSV files found under {BASE_DIR}"
        )

    logger.info(
        "Found %d SCImago CSV file(s)",
        len(files),
    )

    for file_path in files:
        metadata = parse_filename(file_path.name)

        if metadata is None:
            logger.error(
                "Skipping SCImago file with unrecognized filename: %s",
                file_path.name,
            )
            continue

        year = metadata["year"]
        subject_area = metadata["subject_area"]

        file_hash = compute_sha256(file_path)

        dataset = get_or_create_dataset(
            source_code="SCIMAGO",
            dataset_year=year,
            subject_area=subject_area,
            file_name=file_path.name,
            file_hash=file_hash,
        )

        dataset_id = dataset["dataset_id"]
        action = dataset["action"]

        if action == "skip":
            logger.info(
                "Skipping already-loaded SCImago file: %s "
                "(dataset_id=%s)",
                file_path.name,
                dataset_id,
            )
            continue

        logger.info(
            "Importing SCImago file: %s "
            "(dataset_id=%s, action=%s)",
            file_path.name,
            dataset_id,
            action,
        )

        def import_file(cursor: Any) -> int:
            return _import_file(
                cursor,
                dataset_id=dataset_id,
                file_path=file_path,
                file_hash=file_hash,
                year=year,
                subject_area=subject_area,
            )

        run_dataset_import(
            dataset_id,
            import_file,
        )

        logger.info(
            "Loaded SCImago file successfully: %s "
            "(dataset_id=%s)",
            file_path.name,
            dataset_id,
        )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
    )
    ingest_scimago()


if __name__ == "__main__":
    main()
