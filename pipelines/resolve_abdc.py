"""
Resolve ABDC records against canonical journals.

Part 8 of Day 6:
    - process unresolved ABDC records only
    - delegate matching decisions to entity_resolution.resolver
    - pass publisher and rating year as resolver context
    - keep already-decided records idempotent
    - report progress and failures without stopping the whole batch
"""

from __future__ import annotations

from entity_resolution.matching import _get_connection
from entity_resolution.resolver import resolve_record


SOURCE_CODE = "ABDC"
TABLE_NAME = "abdc_records"


def _load_unresolved_records() -> list[tuple]:
    """
    Load every currently unresolved ABDC record.

    The records are loaded before resolution begins so the resolver can
    manage its own database transaction for each individual record.
    """
    connection = _get_connection()

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    id,
                    journal_name,
                    issn,
                    issn_online,
                    publisher,
                    rating_year
                FROM abdc_records
                WHERE journal_id IS NULL
                ORDER BY id
                """
            )

            return cursor.fetchall()
    finally:
        connection.close()


def resolve_abdc() -> None:
    """
    Resolve all currently unresolved ABDC records.

    Each call to resolve_record() owns its own transaction. A failure on
    one source record is therefore isolated from other records.
    """
    records = _load_unresolved_records()

    total = len(records)

    print("ABDC entity resolution")
    print("=" * 60)
    print(f"Unresolved records loaded: {total}")

    if total == 0:
        print("Nothing to resolve.")
        return

    processed = 0
    succeeded = 0
    failed = 0

    for (
        record_id,
        journal_name,
        issn,
        issn_online,
        publisher,
        rating_year,
    ) in records:

        processed += 1

        try:
            resolve_record(
                SOURCE_CODE,
                TABLE_NAME,
                record_id,
                journal_name,
                issn,
                issn_online,
                publisher=publisher,
                observed_year=rating_year,
            )

            succeeded += 1

        except Exception as exc:
            failed += 1

            print(
                f"ERROR: record_id={record_id} "
                f"journal_name={journal_name!r} "
                f"error={type(exc).__name__}: {exc}"
            )

        if processed % 500 == 0 or processed == total:
            print(
                f"Progress: {processed}/{total} "
                f"(succeeded={succeeded}, failed={failed})"
            )

    print("=" * 60)
    print("ABDC entity resolution complete")
    print(f"Processed: {processed}")
    print(f"Succeeded: {succeeded}")
    print(f"Failed: {failed}")


def main() -> None:
    resolve_abdc()


if __name__ == "__main__":
    main()