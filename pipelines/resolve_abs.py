from __future__ import annotations

from entity_resolution.matching import _get_connection
from entity_resolution.resolver import resolve_record


SOURCE_CODE = "ABS"
TABLE_NAME = "abs_records"


def _load_unresolved_records() -> list[tuple]:
    """
    Load every currently unresolved ABS record.

    The resolver owns the transaction for each individual record, so a
    failure on one ABS record does not roll back successful resolutions
    for other records.
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
                    publisher,
                    rating_year
                FROM abs_records
                WHERE journal_id IS NULL
                ORDER BY id
                """
            )

            return cursor.fetchall()

    finally:
        connection.close()


def resolve_abs() -> None:
    """
    Resolve all currently unresolved ABS records.

    Resolution is delegated to the shared Day-6 resolver.

    ABS supplies:
        - journal_name
        - ISSN
        - publisher
        - rating_year

    The shared resolver handles:
        1. existing decisions
        2. ISSN / EISSN matching
        3. exact normalized-title matching
        4. fuzzy-title matching
        5. canonical-journal creation when no sufficiently strong
           existing candidate is found
    """

    records = _load_unresolved_records()

    total = len(records)

    print("ABS entity resolution")
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
                None,
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
    print("ABS entity resolution complete")
    print(f"Processed: {processed}")
    print(f"Succeeded: {succeeded}")
    print(f"Failed: {failed}")


def main() -> None:
    resolve_abs()


if __name__ == "__main__":
    main()