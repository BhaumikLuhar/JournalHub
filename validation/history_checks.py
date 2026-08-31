from __future__ import annotations

import csv
from pathlib import Path

from ingestion.common.pipeline_helpers import _get_connection


REPORT_PATH = Path("reports") / "history_gaps.csv"


FIELDNAMES = [
    "journal_id",
    "canonical_title",
    "missing_year",
    "previous_year",
    "next_year",
]


def _fetch_history_gaps(cursor):
    """
    Find informational SCImago historical continuity gaps.

    A gap is defined as:

        journal has at least one SCImago record in year Y
        AND
        journal has at least one SCImago record in year Y+2
        AND
        journal has no SCImago record in year Y+1

    The check is performed at the canonical-journal level.

    Multiple SCImago subject-area records in the same year do not
    create duplicate gaps because the query uses DISTINCT journal/year
    observations.
    """

    cursor.execute(
        """
        WITH journal_years AS (
            SELECT DISTINCT
                journal_id,
                year
            FROM scimago_records
            WHERE journal_id IS NOT NULL
        ),

        history_gaps AS (
            SELECT
                current_year.journal_id,
                current_year.year AS previous_year,
                current_year.year + 1 AS missing_year,
                current_year.year + 2 AS next_year
            FROM journal_years AS current_year
            INNER JOIN journal_years AS next_year
                ON next_year.journal_id = current_year.journal_id
               AND next_year.year = current_year.year + 2
            LEFT JOIN journal_years AS middle_year
                ON middle_year.journal_id = current_year.journal_id
               AND middle_year.year = current_year.year + 1
            WHERE middle_year.journal_id IS NULL
        )

        SELECT
            gap.journal_id,
            j.canonical_title,
            gap.missing_year,
            gap.previous_year,
            gap.next_year
        FROM history_gaps AS gap
        INNER JOIN journals AS j
            ON j.id = gap.journal_id
        ORDER BY
            gap.journal_id,
            gap.missing_year
        """
    )

    return cursor.fetchall()


def _write_report(rows) -> None:
    """
    Write the informational history-gap report.

    The report is regenerated on every run so that it always reflects
    the current database state.
    """

    REPORT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with REPORT_PATH.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        writer = csv.writer(handle)

        writer.writerow(FIELDNAMES)

        for row in rows:
            writer.writerow(row)


def run_history_checks() -> int:
    """
    Execute the historical continuity validation.

    Returns:
        Number of detected historical gaps.

    IMPORTANT:
        A non-zero gap count is informational and does NOT cause this
        validation to fail. Missing source data for a year is explicitly
        valid in JournalHub.
    """

    print("=" * 72)
    print("JournalHub Day 9 — SCImago History Checks")
    print("=" * 72)

    connection = _get_connection()

    try:
        with connection:
            with connection.cursor() as cursor:
                rows = _fetch_history_gaps(cursor)
    finally:
        connection.close()

    _write_report(rows)

    print()
    print(
        f"Historical gaps detected: {len(rows)}"
    )
    print(
        f"Report: {REPORT_PATH}"
    )

    if rows:
        print()
        print(
            "INFO: These gaps are informational only. "
            "A journal may legitimately be absent from a "
            "SCImago subject-area file for one year."
        )
    else:
        print()
        print(
            "PASS: No SCImago Y / Y+2 continuity gaps detected."
        )

    print()
    print("=" * 72)
    print("SCImago history validation completed.")
    print("=" * 72)

    return len(rows)


def main() -> None:
    run_history_checks()


if __name__ == "__main__":
    main()
