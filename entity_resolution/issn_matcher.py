from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from ingestion.common.pipeline_helpers import _get_connection


REPORT_PATH = Path("reports/issn_conflicts.csv")


def _ensure_report_directory() -> None:
    """Ensure the ISSN conflict report directory exists."""
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)


def _log_conflict(
    normalized_issn: str,
    journal_ids: list[int],
) -> None:
    """
    Append an ISSN conflict to the audit report.

    A conflict occurs when one normalized ISSN/EISSN resolves to more than
    one distinct canonical journal.
    """

    _ensure_report_directory()

    file_exists = REPORT_PATH.exists()

    with REPORT_PATH.open(
        "a",
        newline="",
        encoding="utf-8",
    ) as handle:
        writer = csv.writer(handle)

        if not file_exists:
            writer.writerow(
                [
                    "normalized_issn",
                    "journal_ids",
                ]
            )

        writer.writerow(
            [
                normalized_issn,
                ",".join(
                    str(journal_id)
                    for journal_id in sorted(journal_ids)
                ),
            ]
        )


def match_by_issn(
    normalized_issn: str,
    *,
    connection: Any | None = None,
) -> int | None:
    """
    Resolve a normalized ISSN/EISSN to a canonical journal ID.

    Rules:
        - zero distinct journal IDs -> None
        - exactly one distinct journal ID -> that ID
        - multiple distinct journal IDs -> None + conflict report

    Both ISSN and EISSN identifiers are searched.

    An existing connection may be supplied by a batch caller so multiple
    lookups can reuse one database connection.
    """

    if not normalized_issn:
        return None

    owns_connection = connection is None

    if owns_connection:
        connection = _get_connection()

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT journal_id
                FROM journal_identifiers
                WHERE identifier_type IN ('ISSN', 'EISSN')
                  AND normalized_value = %s
                ORDER BY journal_id
                """,
                (normalized_issn,),
            )

            journal_ids = [
                int(row[0])
                for row in cursor.fetchall()
            ]

        if not journal_ids:
            return None

        if len(journal_ids) == 1:
            return journal_ids[0]

        _log_conflict(
            normalized_issn,
            journal_ids,
        )

        return None

    finally:
        if owns_connection:
            connection.close()