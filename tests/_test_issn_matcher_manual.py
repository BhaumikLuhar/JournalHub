from __future__ import annotations

import csv

from entity_resolution.issn_matcher import (
    REPORT_PATH,
    match_by_issn,
)
from ingestion.common.pipeline_helpers import _get_connection


def _get_known_issn() -> tuple[str, int]:
    """Return one existing normalized ISSN and its journal ID."""

    connection = _get_connection()

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT normalized_value, journal_id
                FROM journal_identifiers
                WHERE identifier_type = 'ISSN'
                ORDER BY id
                LIMIT 1
                """
            )

            row = cursor.fetchone()

            if row is None:
                raise AssertionError(
                    "Expected at least one ISSN identifier."
                )

            return str(row[0]), int(row[1])

    finally:
        connection.close()


def test_unique_match() -> None:
    normalized_issn, expected_journal_id = _get_known_issn()

    actual_journal_id = match_by_issn(
        normalized_issn,
    )

    assert actual_journal_id == expected_journal_id


def test_no_match() -> None:
    assert match_by_issn("9999-9999") is None


def test_empty_value() -> None:
    assert match_by_issn("") is None
    assert match_by_issn(None) is None


def test_conflict_behavior() -> None:
    """
    Create a temporary ISSN/EISSN conflict inside one transaction,
    verify the matcher returns None, then roll everything back.
    """

    connection = _get_connection()

    try:
        cursor = connection.cursor()

        try:
            cursor.execute(
                """
                SELECT id
                FROM journals
                ORDER BY id
                LIMIT 2
                """
            )

            journal_ids = [
                int(row[0])
                for row in cursor.fetchall()
            ]

            assert len(journal_ids) == 2

            test_issn = "9999-9998"

            cursor.execute(
                """
                INSERT INTO journal_identifiers (
                    journal_id,
                    identifier_type,
                    identifier_value,
                    normalized_value,
                    is_primary
                )
                VALUES
                    (%s, 'ISSN', %s, %s, false),
                    (%s, 'EISSN', %s, %s, false)
                """,
                (
                    journal_ids[0],
                    test_issn,
                    test_issn,
                    journal_ids[1],
                    test_issn,
                    test_issn,
                ),
            )

            result = match_by_issn(
                test_issn,
                connection=connection,
            )

            assert result is None

        finally:
            cursor.close()

        # Nothing created by this test may survive.
        connection.rollback()

    finally:
        connection.close()


def test_report_structure() -> None:
    """
    Verify that the Part-5 conflict was appended to the existing conflict
    report.

    The report may already contain historical Day-5 conflict entries, so
    this test must not assume that the first row is a Part-5 header.
    """

    assert REPORT_PATH.exists()

    with REPORT_PATH.open(
        "r",
        newline="",
        encoding="utf-8",
    ) as handle:
        rows = list(csv.reader(handle))

    assert rows

    matching_rows = [
        row
        for row in rows
        if row
        and row[0] == "9999-9998"
    ]

    assert matching_rows, (
        "Expected the temporary Part-5 conflict "
        "9999-9998 to be logged."
    )

    assert matching_rows[-1][0] == "9999-9998"

    journal_ids = {
        int(value)
        for value in matching_rows[-1][1].split(",")
        if value
    }

    assert len(journal_ids) == 2


def main() -> None:
    print("ISSN matcher manual verification")
    print("=" * 60)

    print("Test 1: unique ISSN match")
    test_unique_match()
    print("PASS")

    print("Test 2: no match")
    test_no_match()
    print("PASS")

    print("Test 3: empty value")
    test_empty_value()
    print("PASS")

    print("Test 4: conflicting ISSN/EISSN match")
    test_conflict_behavior()
    print("PASS")

    print("Test 5: conflict report")
    test_report_structure()
    print("PASS")

    print("=" * 60)
    print("ALL ISSN MATCHER ASSERTIONS PASSED")


if __name__ == "__main__":
    main()