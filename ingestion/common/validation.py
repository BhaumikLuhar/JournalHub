"""
Shared validation utilities for JournalHub.

Validation functions identify invalid or suspicious source data without
silently modifying the source representation.

Rejected rows are persisted to ingestion_rejections so that one bad
source row does not necessarily terminate an entire dataset import.
"""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import Any

import psycopg2


logger = logging.getLogger(__name__)


# Secondary human-convenience export.
#
# The PostgreSQL ingestion_rejections table remains the authoritative
# record. This CSV is only an additional report for convenient inspection.
_REJECTION_REPORT_PATH = Path("reports") / "ingestion_rejections.csv"


def validate_issn_checksum(issn8: str) -> bool:
    """
    Validate an 8-character ISSN using the standard Mod-11 checksum.

    ISSN rules:
        - exactly 8 characters
        - first 7 characters are digits
        - final character is a digit or X
        - weights are 8, 7, 6, 5, 4, 3, 2, 1
        - X represents the value 10

    The weighted sum must be divisible by 11.

    Examples:
        0317847X -> valid
        03785955 -> valid
    """
    if issn8 is None:
        return False

    value = str(issn8).strip().upper().replace("-", "")

    if len(value) != 8:
        return False

    if not value[:7].isdigit():
        return False

    if not (value[7].isdigit() or value[7] == "X"):
        return False

    digits = [
        int(character)
        for character in value[:7]
    ]

    check_digit = 10 if value[7] == "X" else int(value[7])

    weighted_sum = sum(
        digit * weight
        for digit, weight in zip(
            digits,
            range(8, 1, -1),
        )
    )

    weighted_sum += check_digit

    return weighted_sum % 11 == 0


def is_in_range(
    value,
    min_v,
    max_v,
) -> bool:
    """
    Return True when value is within the inclusive range.

    Missing, non-numeric, or non-comparable values return False.

    Examples:
        is_in_range(13.110, 0, 100) -> True
        is_in_range(101, 0, 100) -> False
    """
    if value is None:
        return False

    try:
        return min_v <= value <= max_v
    except (TypeError, ValueError):
        return False


def _serialize_raw_row(raw_row: Any) -> Any:
    """
    Prepare a raw row for JSON/CSV reporting.

    This function does not normalize domain values.

    Its only purpose is to make otherwise non-JSON-native scalar values
    representable in the rejection report.
    """
    if raw_row is None:
        return None

    if isinstance(raw_row, dict):
        serialized = {}

        for key, value in raw_row.items():
            if hasattr(value, "isoformat"):
                try:
                    serialized[str(key)] = value.isoformat()
                    continue
                except (TypeError, ValueError):
                    pass

            try:
                json.dumps(value)
                serialized[str(key)] = value
            except (TypeError, ValueError):
                serialized[str(key)] = str(value)

        return serialized

    try:
        json.dumps(raw_row)
        return raw_row
    except (TypeError, ValueError):
        return str(raw_row)


def _append_csv_report(
    dataset_id,
    raw_file_id,
    row_number,
    reason,
    raw_row,
) -> None:
    """
    Append a rejection to the convenience CSV report.

    Database insertion remains the authoritative operation.
    """
    _REJECTION_REPORT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    file_exists = _REJECTION_REPORT_PATH.exists()

    with _REJECTION_REPORT_PATH.open(
        "a",
        encoding="utf-8",
        newline="",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "dataset_id",
                "raw_file_id",
                "row_number",
                "reason",
                "raw_row_snapshot",
            ],
        )

        if not file_exists:
            writer.writeheader()

        writer.writerow(
            {
                "dataset_id": dataset_id,
                "raw_file_id": raw_file_id,
                "row_number": row_number,
                "reason": reason,
                "raw_row_snapshot": json.dumps(
                    _serialize_raw_row(raw_row),
                    ensure_ascii=True,
                    sort_keys=True,
                ),
            }
        )


def _get_database_connection():
    """
    Create a PostgreSQL connection using the project's database/.env file.

    This function intentionally does not hard-code credentials.
    """
    from dotenv import load_dotenv
    import os

    load_dotenv("database/.env")

    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "journal_platform"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def log_rejected_row(
    cursor,
    dataset_id,
    raw_file_id,
    row_number,
    reason,
    raw_row,
) -> None:
    """
    Persist a rejected source row using the caller's existing transaction.

    PostgreSQL ingestion_rejections is authoritative.
    The rejection is inserted using the same cursor/transaction as the
    associated raw_file/raw_row and normalized records.

    The convenience CSV report is written only after the database insert
    succeeds. The caller controls commit/rollback.
    """
    raw_snapshot = _serialize_raw_row(raw_row)
    normalized_reason = str(reason)[:100]

    cursor.execute(
        """
        INSERT INTO ingestion_rejections (
            dataset_id,
            raw_file_id,
            row_number,
            reason,
            raw_row_snapshot
        )
        VALUES (%s, %s, %s, %s, %s::jsonb)
        """,
        (
            dataset_id,
            raw_file_id,
            row_number,
            normalized_reason,
            json.dumps(
                raw_snapshot,
                ensure_ascii=True,
            ),
        ),
    )

    _append_csv_report(
        dataset_id=dataset_id,
        raw_file_id=raw_file_id,
        row_number=row_number,
        reason=normalized_reason,
        raw_row=raw_row,
    )