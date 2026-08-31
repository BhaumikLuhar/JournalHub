from __future__ import annotations

import csv
import os
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
from dotenv import load_dotenv


OUTPUT_PATH = Path("reports") / "ambiguous_matches.csv"


FIELDNAMES = [
    "exported_at",
    "candidate_id",
    "source",
    "source_record_id",
    "source_record_display_name",
    "candidate_journal_id",
    "candidate_journal_title",
    "similarity",
    "issn_match",
    "publisher_match",
    "rank_among_candidates",
    "decision",
]


VALID_DECISIONS = {
    "",
    "accepted",
    "new_journal",
    "rejected_no_match",
}


def _get_connection():
    """
    Open a PostgreSQL connection using the project's database settings.
    """

    load_dotenv("database/.env")

    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "journal_platform"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD"),
    )


def _source_display_expression() -> str:
    """
    Return the source-specific SQL expression used to obtain the
    human-readable source journal name.
    """

    return """
        CASE s.code
            WHEN 'ABDC' THEN a.journal_name
            WHEN 'ABS' THEN x.journal_name
            WHEN 'REPEC' THEN r.journal_name_clean
            ELSE NULL
        END
    """


def _source_join_clause() -> str:
    """
    Return the joins required to retrieve the source record display name.
    """

    return """
        LEFT JOIN abdc_records a
            ON c.source_record_table = 'abdc_records'
           AND c.source_record_id = a.id

        LEFT JOIN abs_records x
            ON c.source_record_table = 'abs_records'
           AND c.source_record_id = x.id

        LEFT JOIN repec_records r
            ON c.source_record_table = 'repec_records'
           AND c.source_record_id = r.id
    """


def _fetch_pending_candidates(cursor):
    """
    Fetch ONLY candidates whose current database review_status is pending.

    The database is the sole source of truth.
    """

    display_expression = _source_display_expression()
    join_clause = _source_join_clause()

    cursor.execute(
        f"""
        SELECT
            c.id AS candidate_id,
            s.code AS source,
            c.source_record_id,
            {display_expression}
                AS source_record_display_name,
            c.candidate_journal_id,
            j.canonical_title
                AS candidate_journal_title,
            c.similarity,
            c.issn_match,
            c.publisher_match,
            c.rank_among_candidates
        FROM entity_match_candidates c
        JOIN sources s
            ON s.id = c.source_id
        JOIN journals j
            ON j.id = c.candidate_journal_id

        {join_clause}

        WHERE c.review_status = 'pending'

        ORDER BY
            c.source_id,
            c.source_record_id,
            c.rank_among_candidates,
            c.id
        """
    )

    return cursor.fetchall()


def _validate_source_display_names(rows) -> None:
    """
    Ensure every pending candidate has a human-readable source name.

    Missing names stop the export instead of producing an unusable
    review CSV.
    """

    missing = [
        (
            row[0],
            row[1],
            row[2],
        )
        for row in rows
        if row[3] is None
        or str(row[3]).strip() == ""
    ]

    if missing:
        preview = missing[:10]

        raise ValueError(
            "Could not determine source_record_display_name for "
            f"{len(missing)} pending candidate(s). "
            f"First examples: {preview!r}"
        )


def _format_timestamp(timestamp: datetime) -> str:
    """
    Format an export timestamp as an explicit UTC ISO-8601 value.
    """

    return (
        timestamp
        .astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def export_pending_candidates() -> int:
    """
    Export the current database-backed pending candidate queue.

    IMPORTANT:
        The exported_at timestamp is generated ONCE for the complete
        export batch. Every CSV row receives the same timestamp.

    The decision column is intentionally blank on export. It is the
    human-review input that will later be consumed by
    apply_review_decisions.py.

    Returns:
        Number of exported pending candidate rows.
    """

    export_timestamp = _format_timestamp(
        datetime.now(timezone.utc)
    )

    connection = _get_connection()

    try:
        with connection:
            with connection.cursor() as cursor:
                rows = _fetch_pending_candidates(
                    cursor
                )

                _validate_source_display_names(
                    rows
                )

    finally:
        connection.close()

    OUTPUT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with OUTPUT_PATH.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:

        writer = csv.DictWriter(
            handle,
            fieldnames=FIELDNAMES,
        )

        writer.writeheader()

        for row in rows:
            (
                candidate_id,
                source,
                source_record_id,
                source_record_display_name,
                candidate_journal_id,
                candidate_journal_title,
                similarity,
                issn_match,
                publisher_match,
                rank_among_candidates,
            ) = row

            writer.writerow(
                {
                    "exported_at": export_timestamp,
                    "candidate_id": candidate_id,
                    "source": source,
                    "source_record_id": source_record_id,
                    "source_record_display_name": (
                        source_record_display_name
                    ),
                    "candidate_journal_id": (
                        candidate_journal_id
                    ),
                    "candidate_journal_title": (
                        candidate_journal_title
                    ),
                    "similarity": similarity,
                    "issn_match": issn_match,
                    "publisher_match": publisher_match,
                    "rank_among_candidates": (
                        rank_among_candidates
                    ),
                    "decision": "",
                }
            )

    print(
        "Pending candidate export complete."
    )
    print(
        f"Rows exported: {len(rows)}"
    )
    print(
        f"Output: {OUTPUT_PATH}"
    )
    print(
        f"Exported at: {export_timestamp}"
    )

    return len(rows)


def main() -> None:
    export_pending_candidates()


if __name__ == "__main__":
    main()