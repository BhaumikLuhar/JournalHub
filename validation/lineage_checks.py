from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from ingestion.common.pipeline_helpers import _get_connection


REPORT_PATH = Path("reports") / "lineage_checks.csv"


# ---------------------------------------------------------------------------
# Source configuration
# ---------------------------------------------------------------------------

SOURCE_CONFIG = {
    "SCIMAGO": {
        "table": "scimago_records",
        "one_to_many": False,
    },
    "ABDC": {
        "table": "abdc_records",
        "one_to_many": False,
    },
    "ABS": {
        "table": "abs_records",
        "one_to_many": True,
    },
    "REPEC": {
        "table": "repec_records",
        "one_to_many": False,
    },
    "FT50": {
        "table": "ft50_records",
        "one_to_many": False,
    },
}


REPORT_FIELDS = [
    "source_code",
    "dataset_id",
    "raw_rows",
    "normalized_records",
    "distinct_source_rows",
    "ingestion_rejections",
    "expected_normalized_count",
    "actual_normalized_count",
    "difference",
    "one_to_many",
    "status",
]


# ---------------------------------------------------------------------------
# Dataset discovery
# ---------------------------------------------------------------------------

def _get_source_id(
    cursor,
    source_code: str,
) -> int:
    """
    Resolve the project's source code to sources.id.
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
            f"Source code {source_code!r} was not found in sources."
        )

    return int(row[0])


def _get_loaded_datasets(
    cursor,
    source_code: str,
) -> list[int]:
    """
    Return every loaded dataset belonging to the requested source.
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


# ---------------------------------------------------------------------------
# Lineage calculation
# ---------------------------------------------------------------------------

def _get_raw_row_count(
    cursor,
    dataset_id: int,
) -> int:
    """
    Count raw source rows belonging to a dataset.

    raw_rows does not contain dataset_id directly.

    The relationship is:

        datasets
            ↓
        raw_files
            ↓
        raw_rows

    Therefore raw rows are counted by joining raw_files.dataset_id.
    """

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM raw_rows rr
        INNER JOIN raw_files rf
            ON rf.id = rr.raw_file_id
        WHERE rf.dataset_id = %s
        """,
        (dataset_id,),
    )

    return int(cursor.fetchone()[0])


def _get_ingestion_rejection_count(
    cursor,
    dataset_id: int,
) -> int:
    """
    Count ingestion rejections belonging to a dataset.
    """

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM ingestion_rejections
        WHERE dataset_id = %s
        """,
        (dataset_id,),
    )

    return int(cursor.fetchone()[0])


def _get_normalized_record_count(
    cursor,
    source_table: str,
    dataset_id: int,
) -> int:
    """
    Count normalized records inserted for a dataset.

    Source table names come only from SOURCE_CONFIG, never from
    user-provided input.
    """

    cursor.execute(
        f"""
        SELECT COUNT(*)
        FROM {source_table}
        WHERE dataset_id = %s
        """,
        (dataset_id,),
    )

    return int(cursor.fetchone()[0])


def _get_distinct_source_row_count(
    cursor,
    source_table: str,
    dataset_id: int,
) -> int:
    """
    Count distinct original source rows represented by normalized
    records.

    This is used only for one-to-many sources such as ABS.
    """

    cursor.execute(
        f"""
        SELECT COUNT(DISTINCT source_row_hash)
        FROM {source_table}
        WHERE dataset_id = %s
        """,
        (dataset_id,),
    )

    return int(cursor.fetchone()[0])


# ---------------------------------------------------------------------------
# Core lineage check
# ---------------------------------------------------------------------------

def check_lineage(
    source_code: str,
    dataset_id: int,
    one_to_many: bool,
    cursor,
) -> dict[str, Any]:
    """
    Validate raw → normalized/rejected lineage for one dataset.

    For one-to-one sources:

        raw_rows
            =
        normalized_records + ingestion_rejections

    For one-to-many sources such as ABS:

        raw_rows
            =
        DISTINCT source_row_hash + ingestion_rejections

    The one_to_many argument is explicit by design because ABS has a
    fundamentally different normalization structure from the other
    sources.
    """

    source_code = source_code.upper()

    if source_code not in SOURCE_CONFIG:
        raise ValueError(
            f"Unsupported source code: {source_code}"
        )

    configured_table = SOURCE_CONFIG[source_code]["table"]
    configured_one_to_many = SOURCE_CONFIG[source_code]["one_to_many"]

    if configured_one_to_many != one_to_many:
        raise ValueError(
            f"one_to_many mismatch for {source_code}: "
            f"configured={configured_one_to_many}, "
            f"received={one_to_many}"
        )

    raw_rows = _get_raw_row_count(
        cursor,
        dataset_id,
    )

    normalized_records = _get_normalized_record_count(
        cursor,
        configured_table,
        dataset_id,
    )

    ingestion_rejections = _get_ingestion_rejection_count(
        cursor,
        dataset_id,
    )

    if one_to_many:
        distinct_source_rows = _get_distinct_source_row_count(
            cursor,
            configured_table,
            dataset_id,
        )

        expected_normalized_count = (
            distinct_source_rows + ingestion_rejections
        )

        actual_normalized_count = raw_rows

    else:
        distinct_source_rows = normalized_records

        expected_normalized_count = (
            normalized_records + ingestion_rejections
        )

        actual_normalized_count = raw_rows

    difference = (
        actual_normalized_count
        - expected_normalized_count
    )

    status = (
        "PASS"
        if difference == 0
        else "FAIL"
    )

    return {
        "source_code": source_code,
        "dataset_id": dataset_id,
        "raw_rows": raw_rows,
        "normalized_records": normalized_records,
        "distinct_source_rows": distinct_source_rows,
        "ingestion_rejections": ingestion_rejections,
        "expected_normalized_count": expected_normalized_count,
        "actual_normalized_count": actual_normalized_count,
        "difference": difference,
        "one_to_many": one_to_many,
        "status": status,
    }


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def _write_report(
    rows: list[dict[str, Any]],
) -> None:
    """
    Write the complete lineage validation report.
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
        writer = csv.DictWriter(
            handle,
            fieldnames=REPORT_FIELDS,
        )

        writer.writeheader()

        for row in rows:
            writer.writerow(row)


# ---------------------------------------------------------------------------
# Main validation
# ---------------------------------------------------------------------------

def run_lineage_checks() -> None:
    """
    Run lineage validation for every loaded dataset of all five sources.
    """

    print("=" * 72)
    print("JournalHub Day 9 — Source-Aware Lineage Checks")
    print("=" * 72)

    connection = _get_connection()

    results: list[dict[str, Any]] = []

    try:
        with connection:
            with connection.cursor() as cursor:

                for source_code, config in SOURCE_CONFIG.items():

                    print()
                    print(
                        f"Checking {source_code} "
                        f"(one_to_many={config['one_to_many']})..."
                    )

                    dataset_ids = _get_loaded_datasets(
                        cursor,
                        source_code,
                    )

                    if not dataset_ids:
                        print(
                            f"WARNING: No loaded datasets found "
                            f"for {source_code}."
                        )
                        continue

                    for dataset_id in dataset_ids:

                        result = check_lineage(
                            source_code=source_code,
                            dataset_id=dataset_id,
                            one_to_many=config["one_to_many"],
                            cursor=cursor,
                        )

                        results.append(result)

                        print(
                            f"  dataset_id={dataset_id} | "
                            f"raw={result['raw_rows']} | "
                            f"normalized={result['normalized_records']} | "
                            f"distinct_source_rows="
                            f"{result['distinct_source_rows']} | "
                            f"rejections="
                            f"{result['ingestion_rejections']} | "
                            f"expected="
                            f"{result['expected_normalized_count']} | "
                            f"difference="
                            f"{result['difference']} | "
                            f"{result['status']}"
                        )

    finally:
        connection.close()

    _write_report(results)

    failures = [
        row
        for row in results
        if row["status"] != "PASS"
    ]

    print()
    print(
        f"Datasets checked: {len(results)}"
    )
    print(
        f"Lineage failures: {len(failures)}"
    )
    print(
        f"Report: {REPORT_PATH}"
    )

    print()

    if failures:
        print(
            "ERROR: One or more lineage checks failed."
        )

        for row in failures:
            print(
                "  "
                f"{row['source_code']} "
                f"dataset_id={row['dataset_id']} "
                f"difference={row['difference']}"
            )

        raise RuntimeError(
            "Source-aware lineage validation failed."
        )

    print(
        "PASS: All loaded datasets satisfied their "
        "source-aware lineage formula."
    )

    # Explicit ABS confirmation required by the Day 9 plan.
    abs_rows = [
        row
        for row in results
        if row["source_code"] == "ABS"
    ]

    print()

    if abs_rows:
        print("ABS source-aware lineage verification:")

        for row in abs_rows:
            print(
                f"  dataset_id={row['dataset_id']} | "
                f"raw_rows={row['raw_rows']} | "
                f"distinct source_row_hash="
                f"{row['distinct_source_rows']} | "
                f"ingestion_rejections="
                f"{row['ingestion_rejections']} | "
                f"difference={row['difference']} | "
                f"{row['status']}"
            )

    print()
    print("=" * 72)
    print("Source-aware lineage validation completed.")
    print("=" * 72)


def main() -> None:
    run_lineage_checks()


if __name__ == "__main__":
    main()
