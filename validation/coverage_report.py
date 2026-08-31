from __future__ import annotations

from pathlib import Path
from typing import Any

from ingestion.common.pipeline_helpers import _get_connection


REPORT_PATH = Path("reports") / "coverage_report.txt"


# ---------------------------------------------------------------------------
# Source configuration
# ---------------------------------------------------------------------------

SOURCE_TABLES = {
    "SCIMAGO": "scimago_records",
    "ABDC": "abdc_records",
    "ABS": "abs_records",
    "REPEC": "repec_records",
    "FT50": "ft50_records",
}


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _get_source_id(
    cursor,
    source_code: str,
) -> int:
    """
    Resolve a source code to sources.id.
    """

    cursor.execute(
        """
        SELECT id
        FROM sources
        WHERE UPPER(code) = UPPER(%s)
        """,
        (source_code,),
    )

    row = cursor.fetchone()

    if row is None:
        raise RuntimeError(
            f"Source {source_code!r} was not found in sources."
        )

    return int(row[0])


def _get_loaded_dataset_ids(
    cursor,
    source_code: str,
) -> list[int]:
    """
    Return all loaded dataset IDs for a source.
    """

    source_id = _get_source_id(
        cursor,
        source_code,
    )

    cursor.execute(
        """
        SELECT id
        FROM datasets
        WHERE source_id = %s
          AND status = 'loaded'
        ORDER BY id
        """,
        (source_id,),
    )

    return [
        int(row[0])
        for row in cursor.fetchall()
    ]


def _get_total_canonical_journals(cursor) -> int:
    """
    Count canonical journals.
    """

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM journals
        """
    )

    return int(cursor.fetchone()[0])


def _get_source_record_count(
    cursor,
    source_table: str,
    dataset_ids: list[int],
) -> int:
    """
    Count source records across all loaded datasets belonging to a source.
    """

    if not dataset_ids:
        return 0

    cursor.execute(
        f"""
        SELECT COUNT(*)
        FROM {source_table}
        WHERE dataset_id = ANY(%s)
        """,
        (dataset_ids,),
    )

    return int(cursor.fetchone()[0])


# ---------------------------------------------------------------------------
# SCImago coverage
# ---------------------------------------------------------------------------

def _get_scimago_coverage(
    cursor,
) -> dict[str, int]:
    """
    SCImago is the seed source.

    Every loaded SCImago source record is expected to have a canonical
    journal_id. We report the total source-record count and the linked
    count explicitly.
    """

    dataset_ids = _get_loaded_dataset_ids(
        cursor,
        "SCIMAGO",
    )

    if not dataset_ids:
        raise RuntimeError(
            "No loaded SCImago datasets found."
        )

    cursor.execute(
        """
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (
                WHERE journal_id IS NOT NULL
            ) AS linked,
            COUNT(*) FILTER (
                WHERE journal_id IS NULL
            ) AS unlinked
        FROM scimago_records
        WHERE dataset_id = ANY(%s)
        """,
        (dataset_ids,),
    )

    total, linked, unlinked = cursor.fetchone()

    return {
        "total": int(total),
        "linked": int(linked),
        "unlinked": int(unlinked),
    }


# ---------------------------------------------------------------------------
# Downstream source coverage
# ---------------------------------------------------------------------------

def _get_decision_counts(
    cursor,
    source_table: str,
    dataset_ids: list[int],
) -> dict[str, int]:
    """
    Count authoritative entity_match_decisions for a source.

    The decisions table is the source of truth for resolution outcomes.
    """

    if not dataset_ids:
        return {
            "accepted": 0,
            "new_journal": 0,
            "rejected_no_match": 0,
        }

    cursor.execute(
        """
        SELECT
            COUNT(*) FILTER (
                WHERE decision = 'accepted'
            ) AS accepted,
            COUNT(*) FILTER (
                WHERE decision = 'new_journal'
            ) AS new_journal,
            COUNT(*) FILTER (
                WHERE decision = 'rejected_no_match'
            ) AS rejected_no_match
        FROM entity_match_decisions
        WHERE source_record_table = %s
          AND source_record_id IN (
              SELECT id
              FROM {source_table}
              WHERE dataset_id = ANY(%s)
          )
        """.format(source_table=source_table),
        (
            source_table,
            dataset_ids,
        ),
    )

    accepted, new_journal, rejected_no_match = cursor.fetchone()

    return {
        "accepted": int(accepted),
        "new_journal": int(new_journal),
        "rejected_no_match": int(rejected_no_match),
    }


def _get_pending_count(
    cursor,
    source_table: str,
    dataset_ids: list[int],
) -> int:
    """
    Count source records that have neither:

        - a resolution decision, nor
        - a canonical journal_id.

    These are the records that are genuinely still pending resolution.

    A record with journal_id NULL and an explicit rejected_no_match
    decision is NOT pending.
    """

    if not dataset_ids:
        return 0

    cursor.execute(
        f"""
        SELECT COUNT(*)
        FROM {source_table} AS source
        WHERE source.dataset_id = ANY(%s)
          AND source.journal_id IS NULL
          AND NOT EXISTS (
              SELECT 1
              FROM entity_match_decisions AS decision
              WHERE decision.source_record_table = %s
                AND decision.source_record_id = source.id
          )
        """,
        (
            dataset_ids,
            source_table,
        ),
    )

    return int(cursor.fetchone()[0])


def _get_coverage_for_source(
    cursor,
    source_code: str,
) -> dict[str, int]:
    """
    Calculate the four-way resolution taxonomy for one downstream source.
    """

    source_table = SOURCE_TABLES[source_code]

    dataset_ids = _get_loaded_dataset_ids(
        cursor,
        source_code,
    )

    source_records = _get_source_record_count(
        cursor,
        source_table,
        dataset_ids,
    )

    decisions = _get_decision_counts(
        cursor,
        source_table,
        dataset_ids,
    )

    pending = _get_pending_count(
        cursor,
        source_table,
        dataset_ids,
    )

    outcome_total = (
        decisions["accepted"]
        + decisions["new_journal"]
        + decisions["rejected_no_match"]
        + pending
    )

    if outcome_total != source_records:
        raise RuntimeError(
            f"Coverage taxonomy does not reconcile for {source_code}: "
            f"source_records={source_records}, "
            f"accepted={decisions['accepted']}, "
            f"new_journal={decisions['new_journal']}, "
            f"rejected_no_match={decisions['rejected_no_match']}, "
            f"pending={pending}, "
            f"sum={outcome_total}"
        )

    return {
        "source_records": source_records,
        "accepted": decisions["accepted"],
        "new_journal": decisions["new_journal"],
        "rejected_no_match": decisions["rejected_no_match"],
        "pending": pending,
    }


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

def _format_report(
    total_journals: int,
    scimago: dict[str, int],
    downstream: dict[str, dict[str, int]],
) -> str:
    """
    Build the required human-readable coverage report.
    """

    lines: list[str] = []

    lines.append(
        "JournalHub Coverage Report"
    )
    lines.append(
        "=" * 72
    )
    lines.append("")

    lines.append(
        f"Total canonical journals: {total_journals}"
    )
    lines.append("")

    lines.append(
        "SCImago: "
        f"{scimago['total']} source records, "
        f"{scimago['linked']} linked"
    )

    if scimago["unlinked"] != 0:
        lines.append(
            f"WARNING: {scimago['unlinked']} SCImago "
            "source records are not linked."
        )

    lines.append("")

    for source_code in (
        "ABDC",
        "ABS",
        "REPEC",
        "FT50",
    ):
        data = downstream[source_code]

        lines.append(
            f"{source_code}: "
            f"matched {data['accepted']} | "
            f"new_journal {data['new_journal']} | "
            f"rejected_no_match {data['rejected_no_match']} | "
            f"still pending {data['pending']}"
        )

    lines.append("")

    lines.append(
        "Pending review status:"
    )

    total_pending = sum(
        data["pending"]
        for data in downstream.values()
    )

    lines.append(
        f"Total downstream records still pending: "
        f"{total_pending}"
    )

    if total_pending == 0:
        lines.append(
            "PASS: No downstream source records remain pending."
        )
    else:
        lines.append(
            "INFO: Pending records remain because the Day 8 "
            "manual-review queue is intentionally deferred."
        )

    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_coverage_report() -> None:
    """
    Generate reports/coverage_report.txt.

    This is read-only with respect to the database.
    """

    print("=" * 72)
    print("JournalHub Day 9 — Coverage Report")
    print("=" * 72)

    connection = _get_connection()

    try:
        with connection:
            with connection.cursor() as cursor:

                print()
                print(
                    "[1/3] Counting canonical journals..."
                )

                total_journals = _get_total_canonical_journals(
                    cursor
                )

                print(
                    f"Total canonical journals: {total_journals}"
                )

                print()
                print(
                    "[2/3] Calculating SCImago seed coverage..."
                )

                scimago = _get_scimago_coverage(
                    cursor
                )

                print(
                    f"SCImago source records: "
                    f"{scimago['total']}"
                )
                print(
                    f"SCImago linked: "
                    f"{scimago['linked']}"
                )
                print(
                    f"SCImago unlinked: "
                    f"{scimago['unlinked']}"
                )

                print()
                print(
                    "[3/3] Calculating downstream source outcomes..."
                )

                downstream: dict[str, dict[str, int]] = {}

                for source_code in (
                    "ABDC",
                    "ABS",
                    "REPEC",
                    "FT50",
                ):
                    data = _get_coverage_for_source(
                        cursor,
                        source_code,
                    )

                    downstream[source_code] = data

                    print(
                        f"{source_code}: "
                        f"records={data['source_records']} | "
                        f"matched={data['accepted']} | "
                        f"new_journal={data['new_journal']} | "
                        f"rejected_no_match="
                        f"{data['rejected_no_match']} | "
                        f"pending={data['pending']}"
                    )

    finally:
        connection.close()

    report = _format_report(
        total_journals,
        scimago,
        downstream,
    )

    REPORT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    REPORT_PATH.write_text(
        report,
        encoding="utf-8",
    )

    print()
    print(
        f"Report written to: {REPORT_PATH}"
    )

    print()
    print(report)

    print("=" * 72)
    print("Coverage report generation completed.")
    print("=" * 72)


def main() -> None:
    run_coverage_report()


if __name__ == "__main__":
    main()
