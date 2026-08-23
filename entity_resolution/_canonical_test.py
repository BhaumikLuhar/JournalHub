from __future__ import annotations
from canonical import (
    get_or_create_canonical_journal,
)

import csv
import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


load_dotenv("database/.env")


TEST_SOURCE_CODE = "SCIMAGO"

TEST_SOURCE_ID = None


def get_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "journal_platform"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD"),
    )


def get_scimago_source_id(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id
            FROM sources
            WHERE code = %s
            """,
            (TEST_SOURCE_CODE,),
        )

        row = cur.fetchone()

        if row is None:
            raise AssertionError(
                "SCIMAGO source row does not exist"
            )

        return row[0]


def assert_zero_test_journals(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*)
            FROM journals
            WHERE canonical_title LIKE 'TEST_CANONICAL_%'
            """
        )

        count = cur.fetchone()[0]

        assert count == 0, (
            f"Expected zero test journals, found {count}"
        )


def test_standalone_creation():
    """
    Verify the original standalone usage still works.
    """
    conn = get_connection()

    try:
        source_id = get_scimago_source_id(conn)
    finally:
        conn.close()

    journal_id, was_created = get_or_create_canonical_journal(
        candidate_title="TEST_CANONICAL_Standalone Journal",
        matching_title="test canonical standalone journal",
        issn_list=["1234-5670"],
        source_id=source_id,
        source_identifier_type="SCIMAGO_SOURCE_ID",
        source_identifier_value="TEST_STANDALONE_001",
        publisher="Test Publisher",
        observed_year=2020,
    )

    assert was_created is True
    assert isinstance(journal_id, int)

    conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    canonical_title,
                    normalized_title,
                    publisher,
                    first_observed_year
                FROM journals
                WHERE id = %s
                """,
                (journal_id,),
            )

            row = cur.fetchone()

            assert row == (
                "TEST_CANONICAL_Standalone Journal",
                "test canonical standalone journal",
                "Test Publisher",
                2020,
            )

            cur.execute(
                """
                SELECT COUNT(*)
                FROM journal_aliases
                WHERE journal_id = %s
                """,
                (journal_id,),
            )

            alias_count = cur.fetchone()[0]

            assert alias_count == 0, (
                "A newly-created journal must not receive a "
                "false alias merely because display and matching "
                "titles differ"
            )

            cur.execute(
                """
                SELECT COUNT(*)
                FROM journal_identifiers
                WHERE journal_id = %s
                  AND identifier_type = 'SCIMAGO_SOURCE_ID'
                  AND normalized_value = 'TEST_STANDALONE_001'
                """,
                (journal_id,),
            )

            assert cur.fetchone()[0] == 1

            cur.execute(
                """
                SELECT COUNT(*)
                FROM journal_identifiers
                WHERE journal_id = %s
                  AND identifier_type = 'ISSN'
                  AND normalized_value = '12345670'
                """,
                (journal_id,),
            )

            assert cur.fetchone()[0] == 1

    finally:
        conn.close()

    return journal_id


def test_standalone_idempotency():
    """
    Calling the helper again with the same Sourceid must find the
    existing journal rather than creating a duplicate.
    """
    conn = get_connection()

    try:
        source_id = get_scimago_source_id(conn)
    finally:
        conn.close()

    journal_id_1, created_1 = get_or_create_canonical_journal(
        candidate_title="TEST_CANONICAL_Standalone Journal",
        matching_title="test canonical standalone journal",
        issn_list=["1234-5670"],
        source_id=source_id,
        source_identifier_type="SCIMAGO_SOURCE_ID",
        source_identifier_value="TEST_STANDALONE_001",
        publisher="Test Publisher",
        observed_year=2020,
    )

    journal_id_2, created_2 = get_or_create_canonical_journal(
        candidate_title="TEST_CANONICAL_Standalone Journal",
        matching_title="test canonical standalone journal",
        issn_list=["1234-5670"],
        source_id=source_id,
        source_identifier_type="SCIMAGO_SOURCE_ID",
        source_identifier_value="TEST_STANDALONE_001",
        publisher="Test Publisher",
        observed_year=2020,
    )

    assert journal_id_1 == journal_id_2
    assert created_1 is False
    assert created_2 is False


def test_transaction_rollback():
    """
    Verify that a caller-owned connection allows the caller to roll
    back the helper's work.
    """
    conn = get_connection()

    try:
        source_id = get_scimago_source_id(conn)

        journal_id, was_created = get_or_create_canonical_journal(
            candidate_title="TEST_CANONICAL_Rollback Journal",
            matching_title="test canonical rollback journal",
            issn_list=["1234-5689"],
            source_id=source_id,
            source_identifier_type="SCIMAGO_SOURCE_ID",
            source_identifier_value="TEST_ROLLBACK_001",
            publisher="Rollback Publisher",
            observed_year=2021,
            conn=conn,
        )

        assert was_created is True
        assert isinstance(journal_id, int)

        # The helper must NOT commit when the caller supplied conn.
        conn.rollback()

    finally:
        conn.close()

    conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*)
                FROM journals
                WHERE canonical_title =
                    'TEST_CANONICAL_Rollback Journal'
                """
            )

            count = cur.fetchone()[0]

            assert count == 0, (
                "Caller-owned transaction rollback did not remove "
                "the journal created by the helper"
            )

    finally:
        conn.close()


def test_transaction_commit():
    """
    Verify that a caller-owned connection persists helper work when
    the caller explicitly commits.
    """
    conn = get_connection()

    try:
        source_id = get_scimago_source_id(conn)

        journal_id, was_created = get_or_create_canonical_journal(
            candidate_title="TEST_CANONICAL_Commit Journal",
            matching_title="test canonical commit journal",
            issn_list=["1234-5697"],
            source_id=source_id,
            source_identifier_type="SCIMAGO_SOURCE_ID",
            source_identifier_value="TEST_COMMIT_001",
            publisher="Commit Publisher",
            observed_year=2022,
            conn=conn,
        )

        assert was_created is True
        assert isinstance(journal_id, int)

        # The helper must not commit. The caller commits.
        conn.commit()

    finally:
        conn.close()

    conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*)
                FROM journals
                WHERE canonical_title =
                    'TEST_CANONICAL_Commit Journal'
                """
            )

            count = cur.fetchone()[0]

            assert count == 1

    finally:
        conn.close()


def test_conflicting_issn_does_not_abort_creation():
    """
    Verify that an ISSN already belonging to another journal does not
    prevent creation of a Sourceid-identified journal.
    """
    conn = get_connection()

    try:
        source_id = get_scimago_source_id(conn)

        # Create the first journal with the test ISSN.
        existing_id, existing_created = (
            get_or_create_canonical_journal(
                candidate_title="TEST_CANONICAL_Existing Journal",
                matching_title="test canonical existing journal",
                issn_list=["1234-5700"],
                source_id=source_id,
                source_identifier_type="SCIMAGO_SOURCE_ID",
                source_identifier_value="TEST_CONFLICT_EXISTING",
                publisher="Existing Publisher",
                observed_year=2020,
            )
        )

        assert existing_created is True

        # Create a second journal with the same ISSN but a different
        # Sourceid. The helper must create the new journal while leaving
        # the conflicting ISSN attached to the first journal.
        new_id, new_created = get_or_create_canonical_journal(
            candidate_title="TEST_CANONICAL_Conflicting Journal",
            matching_title="test canonical conflicting journal",
            issn_list=["1234-5700"],
            source_id=source_id,
            source_identifier_type="SCIMAGO_SOURCE_ID",
            source_identifier_value="TEST_CONFLICT_NEW",
            publisher="New Publisher",
            observed_year=2021,
        )

        assert new_created is True
        assert new_id != existing_id

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT journal_id
                FROM journal_identifiers
                WHERE identifier_type = 'ISSN'
                  AND normalized_value = '12345700'
                """
            )

            row = cur.fetchone()

            assert row is not None
            assert row[0] == existing_id

            cur.execute(
                """
                SELECT COUNT(*)
                FROM journal_identifiers
                WHERE journal_id = %s
                  AND identifier_type = 'SCIMAGO_SOURCE_ID'
                  AND normalized_value = 'TEST_CONFLICT_NEW'
                """,
                (new_id,),
            )

            assert cur.fetchone()[0] == 1

    finally:
        conn.close()


def test_source_identifier_takes_priority():
    """
    Verify that a supplied Sourceid takes priority over a conflicting
    ISSN when resolving an existing journal.
    """
    conn = get_connection()

    try:
        source_id = get_scimago_source_id(conn)

        journal_a, created_a = get_or_create_canonical_journal(
            candidate_title="TEST_CANONICAL_Source Priority A",
            matching_title="test canonical source priority a",
            issn_list=["1234-5719"],
            source_id=source_id,
            source_identifier_type="SCIMAGO_SOURCE_ID",
            source_identifier_value="TEST_PRIORITY_A",
            publisher="Publisher A",
            observed_year=2020,
        )

        assert created_a is True

        journal_b, created_b = get_or_create_canonical_journal(
            candidate_title="TEST_CANONICAL_Source Priority B",
            matching_title="test canonical source priority b",
            issn_list=["1234-5719"],
            source_id=source_id,
            source_identifier_type="SCIMAGO_SOURCE_ID",
            source_identifier_value="TEST_PRIORITY_B",
            publisher="Publisher B",
            observed_year=2021,
        )

        assert created_b is True
        assert journal_a != journal_b

        # Calling again with Sourceid B must resolve to B even though
        # the supplied ISSN belongs to A.
        resolved_id, was_created = get_or_create_canonical_journal(
            candidate_title="TEST_CANONICAL_Source Priority B",
            matching_title="test canonical source priority b",
            issn_list=["1234-5719"],
            source_id=source_id,
            source_identifier_type="SCIMAGO_SOURCE_ID",
            source_identifier_value="TEST_PRIORITY_B",
            publisher="Publisher B",
            observed_year=2021,
        )

        assert resolved_id == journal_b
        assert was_created is False

    finally:
        conn.close()


def test_no_false_alias():
    """
    Explicit regression test for the alias bug fixed in canonical.py.
    """
    conn = get_connection()

    try:
        source_id = get_scimago_source_id(conn)

        journal_id, was_created = get_or_create_canonical_journal(
            candidate_title="TEST_CANONICAL_Alias Regression",
            matching_title="test canonical alias regression",
            issn_list=[],
            source_id=source_id,
            source_identifier_type="SCIMAGO_SOURCE_ID",
            source_identifier_value="TEST_ALIAS_REGRESSION",
            publisher="Alias Publisher",
            observed_year=2023,
        )

        assert was_created is True

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT alias_name, normalized_alias
                FROM journal_aliases
                WHERE journal_id = %s
                """,
                (journal_id,),
            )

            aliases = cur.fetchall()

            assert aliases == [], (
                "False alias detected: canonical display title was "
                "incorrectly inserted as an alias"
            )

    finally:
        conn.close()


def cleanup():
    """
    Remove only the test data created by this script.

    This cleanup is deliberately limited to TEST_CANONICAL_* journals
    and TEST_* identifiers.
    """
    conn = get_connection()

    try:
        with conn:
            with conn.cursor() as cur:

                # First close/remove dependent rows that reference the
                # test journals. These should normally not exist, but
                # cleanup remains explicit.
                cur.execute(
                    """
                    DELETE FROM entity_match_decisions
                    WHERE journal_id IN (
                        SELECT id
                        FROM journals
                        WHERE canonical_title LIKE 'TEST_CANONICAL_%'
                    )
                    """
                )

                cur.execute(
                    """
                    DELETE FROM entity_match_candidates
                    WHERE candidate_journal_id IN (
                        SELECT id
                        FROM journals
                        WHERE canonical_title LIKE 'TEST_CANONICAL_%'
                    )
                    """
                )

                cur.execute(
                    """
                    DELETE FROM journal_source_mapping
                    WHERE journal_id IN (
                        SELECT id
                        FROM journals
                        WHERE canonical_title LIKE 'TEST_CANONICAL_%'
                    )
                    """
                )

                cur.execute(
                    """
                    DELETE FROM journal_aliases
                    WHERE journal_id IN (
                        SELECT id
                        FROM journals
                        WHERE canonical_title LIKE 'TEST_CANONICAL_%'
                    )
                    """
                )

                cur.execute(
                    """
                    DELETE FROM journal_identifiers
                    WHERE journal_id IN (
                        SELECT id
                        FROM journals
                        WHERE canonical_title LIKE 'TEST_CANONICAL_%'
                    )
                    """
                )

                cur.execute(
                    """
                    DELETE FROM journals
                    WHERE canonical_title LIKE 'TEST_CANONICAL_%'
                    """
                )

    finally:
        conn.close()


def main():
    print("Starting canonical helper tests...")

    conn = get_connection()

    try:
        assert_zero_test_journals(conn)
    finally:
        conn.close()

    try:
        journal_id = test_standalone_creation()
        print(
            f"PASS: standalone creation "
            f"(journal_id={journal_id})"
        )

        test_standalone_idempotency()
        print("PASS: standalone Sourceid idempotency")

        test_transaction_rollback()
        print("PASS: caller-owned transaction rollback")

        test_transaction_commit()
        print("PASS: caller-owned transaction commit")

        test_conflicting_issn_does_not_abort_creation()
        print("PASS: conflicting ISSN does not abort creation")

        test_source_identifier_takes_priority()
        print("PASS: Sourceid takes priority over conflicting ISSN")

        test_no_false_alias()
        print("PASS: no false alias on new journal")

    finally:
        cleanup()

    conn = get_connection()

    try:
        assert_zero_test_journals(conn)
    finally:
        conn.close()

    print()
    print("ALL CANONICAL HELPER TESTS PASSED")
    print("All scratch test data was removed.")


if __name__ == "__main__":
    main()
