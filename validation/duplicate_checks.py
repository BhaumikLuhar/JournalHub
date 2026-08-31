from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any

from ingestion.common.pipeline_helpers import _get_connection


REPORT_PATH = Path("reports/issn_conflicts.csv")


# These are the exact PostgreSQL constraints required by Day 9.
IDENTIFIER_UNIQUE_CONSTRAINT = (
    "journal_identifiers_identifier_type_normalized_value_key"
)

SOURCE_MAPPING_UNIQUE_CONSTRAINT = (
    "journal_source_mapping_source_id_source_record_table_source_key"
)


def _constraint_exists(cursor, constraint_name: str) -> bool:
    """
    Return True when the named PostgreSQL constraint exists.

    The check is performed against the current database rather than
    assuming the migration was successfully applied.
    """

    cursor.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM pg_constraint
            WHERE conname = %s
        )
        """,
        (constraint_name,),
    )

    return bool(cursor.fetchone()[0])


def _check_identifier_constraint(cursor) -> None:
    """
    Verify the structural uniqueness constraint on journal identifiers.
    """

    if not _constraint_exists(
        cursor,
        IDENTIFIER_UNIQUE_CONSTRAINT,
    ):
        raise RuntimeError(
            "Required constraint is missing: "
            f"{IDENTIFIER_UNIQUE_CONSTRAINT}"
        )

    cursor.execute(
        """
        SELECT
            identifier_type,
            normalized_value,
            COUNT(*) AS row_count
        FROM journal_identifiers
        GROUP BY
            identifier_type,
            normalized_value
        HAVING COUNT(*) > 1
        ORDER BY
            identifier_type,
            normalized_value
        """
    )

    duplicates = cursor.fetchall()

    if duplicates:
        raise RuntimeError(
            "journal_identifiers contains duplicate "
            "(identifier_type, normalized_value) keys despite "
            "the required UNIQUE constraint. "
            f"Examples: {duplicates[:10]!r}"
        )

    print(
        "PASS: journal_identifiers uniqueness constraint is active "
        "and no duplicate identifier keys exist."
    )


def _check_source_mapping_constraint(cursor) -> None:
    """
    Verify the structural uniqueness constraint on source mappings.
    """

    if not _constraint_exists(
        cursor,
        SOURCE_MAPPING_UNIQUE_CONSTRAINT,
    ):
        raise RuntimeError(
            "Required constraint is missing: "
            f"{SOURCE_MAPPING_UNIQUE_CONSTRAINT}"
        )

    cursor.execute(
        """
        SELECT
            source_id,
            source_record_table,
            source_record_id,
            COUNT(*) AS row_count
        FROM journal_source_mapping
        GROUP BY
            source_id,
            source_record_table,
            source_record_id
        HAVING COUNT(*) > 1
        ORDER BY
            source_id,
            source_record_table,
            source_record_id
        """
    )

    duplicates = cursor.fetchall()

    if duplicates:
        raise RuntimeError(
            "journal_source_mapping contains duplicate "
            "(source_id, source_record_table, source_record_id) "
            "keys despite the required UNIQUE constraint. "
            f"Examples: {duplicates[:10]!r}"
        )

    print(
        "PASS: journal_source_mapping uniqueness constraint is active "
        "and no duplicate source-record mappings exist."
    )


def _read_conflict_rows() -> list[dict[str, str]]:
    """
    Read the existing ISSN conflict audit report.

    The project currently has more than one historical CSV message
    format, so this function accepts either:
        - a one-column 'message' report
        - a structured report containing normalized_issn/journal_ids
    """

    if not REPORT_PATH.exists():
        print(
            "INFO: reports/issn_conflicts.csv does not exist. "
            "No recorded identifier conflicts to inspect."
        )
        return []

    with REPORT_PATH.open(
        "r",
        encoding="utf-8",
        newline="",
    ) as handle:
        reader = csv.DictReader(handle)

        if reader.fieldnames is None:
            raise RuntimeError(
                f"{REPORT_PATH} has no CSV header."
            )

        rows: list[dict[str, str]] = []

        for row in reader:
            cleaned: dict[str, str] = {}

            for key, value in row.items():
                if key is None:
                    continue

                if isinstance(value, list):
                    cleaned[key] = ",".join(
                        str(v) for v in value
                    ).strip()
                else:
                    cleaned[key] = (value or "").strip()

            rows.append(cleaned)

        return rows


def _extract_journal_ids(row: dict[str, str]) -> list[int]:
    """
    Extract canonical journal IDs from the currently supported
    conflict-report formats.

    Returns an empty list when the report does not contain enough
    structured information to identify the journals.
    """

    journal_ids: list[int] = []

    structured_ids = row.get("journal_ids", "").strip()

    if structured_ids:
        for value in structured_ids.split(","):
            value = value.strip()

            if value.isdigit():
                journal_ids.append(int(value))

        if journal_ids:
            return sorted(set(journal_ids))

    message = row.get("message", "")

    if not message:
        return []

    # Current canonical.py message format:
    #
    # "ISSN conflict: source record matched multiple journals [1, 2]"
    match = re.search(
        r"journals\s+\[([^\]]+)\]",
        message,
    )

    if match:
        for value in match.group(1).split(","):
            value = value.strip()

            if value.isdigit():
                journal_ids.append(int(value))

        if journal_ids:
            return sorted(set(journal_ids))

    # Historical identifier-conflict messages contain one existing
    # journal and one newly-created journal:
    #
    # "... already belongs to journal 7, could not attach ..."
    match = re.search(
        r"already belongs to journal\s+(\d+)",
        message,
    )

    if match:
        journal_ids.append(int(match.group(1)))

    match = re.search(
        r"newly created journal\s+(\d+)",
        message,
    )

    if match:
        journal_ids.append(int(match.group(1)))

    return sorted(set(journal_ids))


def _pending_candidates_for_journals(
    cursor,
    journal_ids: list[int],
) -> set[int]:
    """
    Return canonical journal IDs that currently appear in pending
    entity-match candidates.
    """

    if not journal_ids:
        return set()

    cursor.execute(
        """
        SELECT DISTINCT candidate_journal_id
        FROM entity_match_candidates
        WHERE review_status = 'pending'
          AND candidate_journal_id = ANY(%s)
        """,
        (journal_ids,),
    )

    return {
        int(row[0])
        for row in cursor.fetchall()
    }


def _inspect_conflict_report(
    cursor,
) -> None:
    """
    Inspect every recorded ISSN conflict.

    A conflict is considered currently supported when at least one
    referenced journal remains represented in the pending review
    candidate table.

    When the report contains insufficient information to connect a
    historical conflict to a pending candidate, the script reports
    that limitation explicitly rather than incorrectly declaring the
    conflict resolved.
    """

    rows = _read_conflict_rows()

    if not rows:
        print(
            "PASS: ISSN conflict report contains no rows requiring review."
        )
        return

    unresolved_or_unverifiable = 0
    pending_supported = 0

    for row_number, row in enumerate(rows, start=2):
        journal_ids = _extract_journal_ids(row)

        if not journal_ids:
            unresolved_or_unverifiable += 1

            print(
                "WARN: ISSN conflict row "
                f"{row_number} cannot be linked to specific journal IDs; "
                "manual resolution status cannot be inferred from the "
                "current report format."
            )
            continue

        pending_ids = _pending_candidates_for_journals(
            cursor,
            journal_ids,
        )

        if pending_ids:
            pending_supported += 1

            print(
                "INFO: ISSN conflict row "
                f"{row_number} has pending candidate evidence for "
                f"journal IDs {sorted(pending_ids)}."
            )
        else:
            # We deliberately do NOT call this "resolved".
            #
            # Absence from entity_match_candidates does not prove a
            # historical conflict was manually reviewed. The existing
            # report does not retain enough source-record information
            # to establish that fact.
            unresolved_or_unverifiable += 1

            print(
                "WARN: ISSN conflict row "
                f"{row_number} has no currently pending candidate "
                f"for referenced journal IDs {journal_ids}. "
                "The existing conflict report does not contain enough "
                "information to prove manual resolution."
            )

    print(
        f"ISSN conflict rows inspected: {len(rows)}"
    )
    print(
        f"Conflict rows with pending candidate evidence: "
        f"{pending_supported}"
    )
    print(
        f"Conflict rows unresolved/unverifiable from available evidence: "
        f"{unresolved_or_unverifiable}"
    )


def run_duplicate_checks() -> None:
    """
    Execute the complete Day 9 duplicate/integrity validation.

    This function is intentionally read-only.
    """

    print("=" * 72)
    print("JournalHub Day 9 — Duplicate / Integrity Checks")
    print("=" * 72)

    connection = _get_connection()

    try:
        with connection:
            with connection.cursor() as cursor:
                print()
                print("[1/3] Checking journal identifier uniqueness...")
                _check_identifier_constraint(cursor)

                print()
                print("[2/3] Checking source mapping uniqueness...")
                _check_source_mapping_constraint(cursor)

                print()
                print("[3/3] Inspecting ISSN conflict report...")
                _inspect_conflict_report(cursor)

    finally:
        connection.close()

    print()
    print("=" * 72)
    print("Duplicate / integrity validation completed.")
    print("=" * 72)


def main() -> None:
    run_duplicate_checks()


if __name__ == "__main__":
    main()
