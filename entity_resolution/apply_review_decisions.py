from __future__ import annotations

import csv
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg2
from dotenv import load_dotenv

from entity_resolution.canonical import (
    get_or_create_canonical_journal,
)
from entity_resolution.matching import (
    accept_candidate_and_close_siblings,
)


DEFAULT_CSV_PATH = (
    Path("reports") / "ambiguous_matches.csv"
)

STALE_OUTPUT_PATH = (
    Path("reports") / "stale_review_rows.csv"
)

VALID_DECISIONS = {
    "accepted",
    "new_journal",
    "rejected_no_match",
}

REQUIRED_COLUMNS = {
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
}

ALLOWED_SOURCE_TABLES = {
    "abdc_records",
    "abs_records",
    "repec_records",
}

SOURCE_CODE_TO_TABLE = {
    "ABDC": "abdc_records",
    "ABS": "abs_records",
    "REPEC": "repec_records",
}


# ------------------------------------------------------------------
# Database
# ------------------------------------------------------------------


def _get_connection():
    """
    Open a PostgreSQL connection using the project's database/.env.
    """

    load_dotenv("database/.env")

    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv(
            "DB_NAME",
            "journal_platform",
        ),
        user=os.getenv(
            "DB_USER",
            "postgres",
        ),
        password=os.getenv("DB_PASSWORD"),
    )


# ------------------------------------------------------------------
# General helpers
# ------------------------------------------------------------------


def _reviewed_by() -> str:
    """
    Return the reviewer name used for manually applied decisions.

    The value can be supplied through REVIEWED_BY.
    """

    value = os.getenv(
        "REVIEWED_BY",
        "",
    ).strip()

    if not value:
        raise ValueError(
            "REVIEWED_BY environment variable is required."
        )

    return value


def _utc_timestamp() -> datetime:
    """
    Return the current UTC timestamp.
    """

    return datetime.now(timezone.utc)


def _normalize_decision(value: str) -> str:
    """
    Normalize and validate a CSV decision value.
    """

    decision = value.strip().lower()

    if decision not in VALID_DECISIONS:
        raise ValueError(
            f"Invalid decision {value!r}. "
            f"Expected one of: "
            f"{sorted(VALID_DECISIONS)!r}"
        )

    return decision


def _parse_positive_int(
    value: str,
    field_name: str,
) -> int:
    """
    Parse a required positive integer CSV field.
    """

    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{field_name} must be an integer; "
            f"got {value!r}"
        ) from exc

    if parsed <= 0:
        raise ValueError(
            f"{field_name} must be positive; "
            f"got {parsed}"
        )

    return parsed


# ------------------------------------------------------------------
# CSV loading / validation
# ------------------------------------------------------------------


def _read_csv(
    csv_path: Path,
) -> list[dict[str, str]]:
    """
    Read the review CSV.

    The CSV is treated as user-editable input, so its structure is
    validated before any database mutation occurs.
    """

    if not csv_path.exists():
        raise FileNotFoundError(
            f"Review CSV does not exist: {csv_path}"
        )

    if not csv_path.is_file():
        raise ValueError(
            f"Review CSV is not a file: {csv_path}"
        )

    with csv_path.open(
        "r",
        encoding="utf-8",
        newline="",
    ) as handle:

        reader = csv.DictReader(handle)

        if reader.fieldnames is None:
            raise ValueError(
                "Review CSV has no header row."
            )

        actual_columns = set(
            reader.fieldnames
        )

        missing_columns = (
            REQUIRED_COLUMNS
            - actual_columns
        )

        if missing_columns:
            raise ValueError(
                "Review CSV is missing required "
                f"columns: {sorted(missing_columns)!r}"
            )

        rows = list(reader)

    return rows


def _validate_row_structure(
    row: dict[str, str],
    row_number: int,
) -> dict[str, Any] | None:
    """
    Validate and normalize one CSV row.

    Blank decision means the row has not been reviewed yet and is
    therefore ignored by the application stage.
    """

    decision = (
        row.get("decision") or ""
    ).strip().lower()

    if not decision:
        return None

    if decision not in VALID_DECISIONS:
        raise ValueError(
            f"CSV row {row_number}: invalid decision "
            f"{row.get('decision')!r}"
        )

    candidate_id = _parse_positive_int(
        row.get("candidate_id", ""),
        "candidate_id",
    )

    source_record_id = _parse_positive_int(
        row.get("source_record_id", ""),
        "source_record_id",
    )

    candidate_journal_id = _parse_positive_int(
        row.get("candidate_journal_id", ""),
        "candidate_journal_id",
    )

    source = (
        row.get("source") or ""
    ).strip().upper()

    if source not in SOURCE_CODE_TO_TABLE:
        raise ValueError(
            f"CSV row {row_number}: unsupported source "
            f"{source!r}"
        )

    source_record_table = (
        SOURCE_CODE_TO_TABLE[source]
    )

    return {
        "row_number": row_number,
        "candidate_id": candidate_id,
        "source": source,
        "source_record_table": source_record_table,
        "source_record_id": source_record_id,
        "candidate_journal_id": candidate_journal_id,
        "decision": decision,
        "source_record_display_name": (
            row.get(
                "source_record_display_name",
                "",
            ).strip()
        ),
        "candidate_journal_title": (
            row.get(
                "candidate_journal_title",
                "",
            ).strip()
        ),
        "exported_at": (
            row.get(
                "exported_at",
                "",
            ).strip()
        ),
    }


def _validate_decision_groups(
    decision_rows: list[dict[str, Any]],
) -> None:
    """
    Validate that the CSV does not contain conflicting decisions for
    the same source record.

    Rules:

        - One source record may have only one logical decision.
        - accepted must identify exactly one candidate.
        - new_journal / rejected_no_match may be placed on any one
          candidate row within the source-record group.
        - Two different accepted candidate IDs for one source record
          are invalid.
    """

    groups: dict[
        tuple[str, int],
        list[dict[str, Any]],
    ] = defaultdict(list)

    for row in decision_rows:
        groups[
            (
                row["source_record_table"],
                row["source_record_id"],
            )
        ].append(row)

    for (
        source_key,
        rows,
    ) in groups.items():

        decisions = {
            row["decision"]
            for row in rows
        }

        if len(decisions) > 1:
            raise ValueError(
                "Conflicting decisions found for "
                f"{source_key!r}: {sorted(decisions)!r}"
            )

        decision = next(
            iter(decisions)
        )

        if decision == "accepted":
            candidate_ids = {
                row["candidate_id"]
                for row in rows
            }

            if len(candidate_ids) != 1:
                raise ValueError(
                    "An accepted decision must identify exactly "
                    "one candidate for source record "
                    f"{source_key!r}; found candidate IDs "
                    f"{sorted(candidate_ids)!r}"
                )


# ------------------------------------------------------------------
# Candidate locking / stale detection
# ------------------------------------------------------------------


def _lock_candidate(
    cursor,
    candidate_id: int,
):
    """
    Lock and retrieve the candidate.

    The lock makes the stale-status check and subsequent mutation
    part of the same serialized transaction.
    """

    cursor.execute(
        """
        SELECT
            id,
            source_id,
            source_record_table,
            source_record_id,
            candidate_journal_id,
            similarity,
            match_method,
            review_status
        FROM entity_match_candidates
        WHERE id = %s
        FOR UPDATE
        """,
        (candidate_id,),
    )

    return cursor.fetchone()


def _get_source_id(
    cursor,
    source: str,
) -> int:
    """
    Resolve source code to source ID.
    """

    cursor.execute(
        """
        SELECT id
        FROM sources
        WHERE code = %s
        """,
        (source,),
    )

    row = cursor.fetchone()

    if row is None:
        raise ValueError(
            f"Unknown source code: {source!r}"
        )

    return int(row[0])


def _verify_csv_matches_database_candidate(
    candidate_row,
    decision_row: dict[str, Any],
) -> None:
    """
    Verify that immutable identifying fields in the CSV still match
    the candidate currently stored in the database.

    This protects against a CSV being edited to point a candidate at
    a different source record or source.
    """

    (
        candidate_id,
        source_id,
        source_record_table,
        source_record_id,
        candidate_journal_id,
        _similarity,
        _match_method,
        _review_status,
    ) = candidate_row

    if (
        int(candidate_id)
        != decision_row["candidate_id"]
    ):
        raise ValueError(
            "Candidate ID mismatch."
        )

    expected_table = (
        decision_row["source_record_table"]
    )

    if source_record_table != expected_table:
        raise ValueError(
            "CSV source-record table does not match "
            f"database candidate {candidate_id}: "
            f"CSV={expected_table!r}, "
            f"DB={source_record_table!r}"
        )

    if (
        int(source_record_id)
        != decision_row["source_record_id"]
    ):
        raise ValueError(
            "CSV source_record_id does not match "
            f"database candidate {candidate_id}."
        )

    if (
        int(candidate_journal_id)
        != decision_row["candidate_journal_id"]
    ):
        raise ValueError(
            "CSV candidate_journal_id does not match "
            f"database candidate {candidate_id}."
        )

    if source_id is None:
        raise ValueError(
            f"Candidate {candidate_id} has NULL source_id."
        )


# ------------------------------------------------------------------
# Decision recording helpers
# ------------------------------------------------------------------


def _insert_entity_match_decision(
    cursor,
    *,
    source_id: int,
    source_record_table: str,
    source_record_id: int,
    journal_id: int | None,
    match_method: str,
    confidence: float | None,
    decision: str,
    reviewed_by: str,
) -> None:
    """
    Insert the authoritative decision record.

    The unique constraint guarantees one decision per source record.
    """

    cursor.execute(
        """
        INSERT INTO entity_match_decisions (
            source_id,
            source_record_table,
            source_record_id,
            journal_id,
            match_method,
            confidence,
            decision,
            reviewed_by,
            reviewed_at
        )
        VALUES (
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s
        )
        """,
        (
            source_id,
            source_record_table,
            source_record_id,
            journal_id,
            match_method,
            confidence,
            decision,
            reviewed_by,
            _utc_timestamp(),
        ),
    )


def _update_source_record(
    cursor,
    *,
    source_record_table: str,
    source_record_id: int,
    journal_id: int | None,
) -> None:
    """
    Update the source record's canonical journal ID.

    The table name is selected only from the explicit allow-list.
    """

    if (
        source_record_table
        not in ALLOWED_SOURCE_TABLES
    ):
        raise ValueError(
            "Unsupported source record table: "
            f"{source_record_table!r}"
        )

    cursor.execute(
        f"""
        UPDATE {source_record_table}
        SET journal_id = %s
        WHERE id = %s
        """,
        (
            journal_id,
            source_record_id,
        ),
    )

    if cursor.rowcount != 1:
        raise ValueError(
            f"Expected exactly one source record update for "
            f"{source_record_table}:{source_record_id}; "
            f"updated {cursor.rowcount} rows."
        )


def _insert_source_mapping(
    cursor,
    *,
    journal_id: int,
    source_id: int,
    source_record_table: str,
    source_record_id: int,
    match_method: str,
    match_score: float | None,
) -> None:
    """
    Insert the source mapping.

    The database uniqueness constraint prevents duplicate mappings.
    """

    cursor.execute(
        """
        INSERT INTO journal_source_mapping (
            journal_id,
            source_id,
            source_record_table,
            source_record_id,
            match_method,
            match_score,
            match_status
        )
        VALUES (
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            'confirmed'
        )
        ON CONFLICT (
            source_id,
            source_record_table,
            source_record_id
        )
        DO NOTHING
        """,
        (
            journal_id,
            source_id,
            source_record_table,
            source_record_id,
            match_method,
            match_score,
        ),
    )


# ------------------------------------------------------------------
# Rejection helpers
# ------------------------------------------------------------------


def _reject_all_pending_siblings(
    cursor,
    *,
    source_id: int,
    source_record_table: str,
    source_record_id: int,
    reviewed_by: str,
) -> None:
    """
    Close all pending candidates for a source record as rejected.
    """

    cursor.execute(
        """
        UPDATE entity_match_candidates
        SET
            review_status = 'rejected',
            reviewed_by = %s,
            reviewed_at = %s
        WHERE source_id = %s
          AND source_record_table = %s
          AND source_record_id = %s
          AND review_status = 'pending'
        """,
        (
            reviewed_by,
            _utc_timestamp(),
            source_id,
            source_record_table,
            source_record_id,
        ),
    )


# ------------------------------------------------------------------
# Accepted decision
# ------------------------------------------------------------------


def _apply_accepted(
    cursor,
    *,
    candidate_id: int,
    candidate_row,
    decision_row: dict[str, Any],
    reviewed_by: str,
) -> None:
    """
    Apply an accepted candidate decision.
    """

    (
        _candidate_id,
        source_id,
        source_record_table,
        source_record_id,
        candidate_journal_id,
        similarity,
        match_method,
        review_status,
    ) = candidate_row

    if review_status != "pending":
        raise RuntimeError(
            "Candidate became non-pending before acceptance."
        )

    if int(source_id) != _get_source_id(
        cursor,
        decision_row["source"],
    ):
        raise ValueError(
            f"Candidate {candidate_id} source mismatch."
        )

    # Shared helper:
    #
    #   - locks candidate again safely
    #   - accepts selected candidate
    #   - closes pending siblings
    #
    # The current transaction remains caller-owned.
    accept_candidate_and_close_siblings(
        candidate_id=candidate_id,
        reviewed_by=reviewed_by,
        conn=cursor.connection,
    )

    # The selected candidate is now authoritative.
    _update_source_record(
        cursor,
        source_record_table=source_record_table,
        source_record_id=int(source_record_id),
        journal_id=int(candidate_journal_id),
    )

    _insert_source_mapping(
        cursor,
        journal_id=int(candidate_journal_id),
        source_id=int(source_id),
        source_record_table=source_record_table,
        source_record_id=int(source_record_id),
        match_method="manually_confirmed",
        match_score=(
            float(similarity)
            if similarity is not None
            else None
        ),
    )

    _insert_entity_match_decision(
        cursor,
        source_id=int(source_id),
        source_record_table=source_record_table,
        source_record_id=int(source_record_id),
        journal_id=int(candidate_journal_id),
        match_method="manually_confirmed",
        confidence=(
            float(similarity)
            if similarity is not None
            else None
        ),
        decision="manually_confirmed",
        reviewed_by=reviewed_by,
    )


# ------------------------------------------------------------------
# Rejected-no-match decision
# ------------------------------------------------------------------


def _apply_rejected_no_match(
    cursor,
    *,
    candidate_row,
    reviewed_by: str,
) -> None:
    """
    Apply a rejected_no_match decision to the complete source-record
    candidate group.
    """

    (
        _candidate_id,
        source_id,
        source_record_table,
        source_record_id,
        _candidate_journal_id,
        _similarity,
        _match_method,
        _review_status,
    ) = candidate_row

    _reject_all_pending_siblings(
        cursor,
        source_id=int(source_id),
        source_record_table=source_record_table,
        source_record_id=int(source_record_id),
        reviewed_by=reviewed_by,
    )

    _insert_entity_match_decision(
        cursor,
        source_id=int(source_id),
        source_record_table=source_record_table,
        source_record_id=int(source_record_id),
        journal_id=None,
        match_method="manual_review",
        confidence=None,
        decision="rejected_no_match",
        reviewed_by=reviewed_by,
    )


# ------------------------------------------------------------------
# New-journal decision
# ------------------------------------------------------------------


def _apply_new_journal(
    cursor,
    *,
    candidate_row,
    decision_row: dict[str, Any],
    reviewed_by: str,
) -> None:
    """
    Apply a new_journal decision.

    Candidate rows are first closed as rejected, then the canonical
    journal is obtained/created using the existing conflict-tolerant
    helper.
    """

    (
        _candidate_id,
        source_id,
        source_record_table,
        source_record_id,
        _candidate_journal_id,
        _similarity,
        _match_method,
        _review_status,
    ) = candidate_row

    source_record_id = int(
        source_record_id
    )

    # Read the current source record's canonical source fields.
    if source_record_table == "abdc_records":
        cursor.execute(
            """
            SELECT journal_name, NULL::integer
            FROM abdc_records
            WHERE id = %s
            """,
            (source_record_id,),
        )

    elif source_record_table == "abs_records":
        cursor.execute(
            """
            SELECT journal_name, NULL::integer
            FROM abs_records
            WHERE id = %s
            """,
            (source_record_id,),
        )

    elif source_record_table == "repec_records":
        cursor.execute(
            """
            SELECT journal_name_clean, NULL::integer
            FROM repec_records
            WHERE id = %s
            """,
            (source_record_id,),
        )

    else:
        raise ValueError(
            "Unsupported source record table: "
            f"{source_record_table!r}"
        )

    source_record = cursor.fetchone()

    if source_record is None:
        raise ValueError(
            f"Source record not found: "
            f"{source_record_table}:{source_record_id}"
        )

    source_title = (
        str(source_record[0]).strip()
        if source_record[0] is not None
        else ""
    )

    if not source_title:
        raise ValueError(
            f"Source record has no journal title: "
            f"{source_record_table}:{source_record_id}"
        )

    # FT50 is not part of this manual queue, and the current review
    # queue is ABDC/ABS/RePEc. None of these source records supplies
    # an identifier here, so use the conflict-tolerant canonical helper
    # with no ISSN values.
    journal_id, _created = (
        get_or_create_canonical_journal(
            candidate_title=source_title,
            matching_title=source_title,
            issn_list=[],
            source_id=int(source_id),
            publisher=None,
            observed_year=None,
            conn=cursor.connection,
        )
    )

    _reject_all_pending_siblings(
        cursor,
        source_id=int(source_id),
        source_record_table=source_record_table,
        source_record_id=source_record_id,
        reviewed_by=reviewed_by,
    )

    _update_source_record(
        cursor,
        source_record_table=source_record_table,
        source_record_id=source_record_id,
        journal_id=int(journal_id),
    )

    _insert_source_mapping(
        cursor,
        journal_id=int(journal_id),
        source_id=int(source_id),
        source_record_table=source_record_table,
        source_record_id=source_record_id,
        match_method="new_journal",
        match_score=1.0,
    )

    _insert_entity_match_decision(
        cursor,
        source_id=int(source_id),
        source_record_table=source_record_table,
        source_record_id=source_record_id,
        journal_id=int(journal_id),
        match_method="new_journal",
        confidence=1.0,
        decision="new_journal",
        reviewed_by=reviewed_by,
    )


# ------------------------------------------------------------------
# One decision transaction
# ------------------------------------------------------------------


def _apply_one_decision(
    decision_row: dict[str, Any],
    *,
    reviewed_by: str,
) -> tuple[str, str | None]:
    """
    Apply one logical source-record decision.

    Returns:

        ("applied", None)
        ("stale", current_status)
    """

    connection = _get_connection()

    try:
        with connection:
            with connection.cursor() as cursor:

                candidate_id = (
                    decision_row["candidate_id"]
                )

                candidate_row = _lock_candidate(
                    cursor,
                    candidate_id,
                )

                if candidate_row is None:
                    raise ValueError(
                        f"Candidate {candidate_id} does not exist."
                    )

                _verify_csv_matches_database_candidate(
                    candidate_row,
                    decision_row,
                )

                current_status = (
                    candidate_row[-1]
                )

                # --------------------------------------------------
                # STALE-CSV PROTECTION
                # --------------------------------------------------
                if current_status != "pending":
                    return (
                        "stale",
                        str(current_status),
                    )

                decision = (
                    decision_row["decision"]
                )

                if decision == "accepted":
                    _apply_accepted(
                        cursor,
                        candidate_id=candidate_id,
                        candidate_row=candidate_row,
                        decision_row=decision_row,
                        reviewed_by=reviewed_by,
                    )

                elif decision == "rejected_no_match":
                    _apply_rejected_no_match(
                        cursor,
                        candidate_row=candidate_row,
                        reviewed_by=reviewed_by,
                    )

                elif decision == "new_journal":
                    _apply_new_journal(
                        cursor,
                        candidate_row=candidate_row,
                        decision_row=decision_row,
                        reviewed_by=reviewed_by,
                    )

                else:
                    raise ValueError(
                        f"Unsupported decision: {decision!r}"
                    )

                return (
                    "applied",
                    None,
                )

    finally:
        connection.close()


# ------------------------------------------------------------------
# Stale report
# ------------------------------------------------------------------


def _write_stale_report(
    rows: list[dict[str, str]],
) -> None:
    """
    Write stale candidate information.

    Only stale rows from the current application run are written.
    """

    STALE_OUTPUT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with STALE_OUTPUT_PATH.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:

        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "candidate_id",
                "csv_decision",
                "current_db_status",
            ],
        )

        writer.writeheader()

        writer.writerows(rows)


# ------------------------------------------------------------------
# Main application
# ------------------------------------------------------------------


def apply_review_decisions(
    csv_path: Path,
) -> None:
    """
    Apply all non-blank decisions from a review CSV.

    Blank decision rows are intentionally skipped.

    Every logical decision is applied in its own database transaction.
    """

    reviewed_by = _reviewed_by()

    rows = _read_csv(
        csv_path
    )

    decision_rows: list[
        dict[str, Any]
    ] = []

    for row_number, row in enumerate(
        rows,
        start=2,
    ):
        parsed = _validate_row_structure(
            row,
            row_number,
        )

        if parsed is not None:
            decision_rows.append(
                parsed
            )

    _validate_decision_groups(
        decision_rows
    )

    stale_rows: list[
        dict[str, str]
    ] = []

    applied = 0
    stale = 0

    print(
        "Review decision application"
    )
    print(
        "=" * 60
    )
    print(
        f"CSV rows: {len(rows)}"
    )
    print(
        f"Decision rows: {len(decision_rows)}"
    )
    print(
        f"Reviewer: {reviewed_by}"
    )

    for index, decision_row in enumerate(
        decision_rows,
        start=1,
    ):

        status, current_db_status = (
            _apply_one_decision(
                decision_row,
                reviewed_by=reviewed_by,
            )
        )

        if status == "stale":
            stale += 1

            stale_rows.append(
                {
                    "candidate_id": str(
                        decision_row[
                            "candidate_id"
                        ]
                    ),
                    "csv_decision": decision_row[
                        "decision"
                    ],
                    "current_db_status": (
                        current_db_status
                        or ""
                    ),
                }
            )

            print(
                f"STALE: candidate_id="
                f"{decision_row['candidate_id']} "
                f"csv_decision="
                f"{decision_row['decision']} "
                f"db_status="
                f"{current_db_status}"
            )

        else:
            applied += 1

        if (
            index % 25 == 0
            or index == len(decision_rows)
        ):
            print(
                f"Progress: {index}/"
                f"{len(decision_rows)} "
                f"(applied={applied}, "
                f"stale={stale})"
            )

    _write_stale_report(
        stale_rows
    )

    print(
        "=" * 60
    )
    print(
        "Review decision application complete"
    )
    print(
        f"Applied: {applied}"
    )
    print(
        f"Stale/skipped: {stale}"
    )
    print(
        f"Stale report: {STALE_OUTPUT_PATH}"
    )


def main() -> None:
    if len(sys.argv) > 2:
        print(
            "Usage: python -m "
            "entity_resolution.apply_review_decisions "
            "[review_csv]"
        )
        raise SystemExit(2)

    csv_path = (
        Path(sys.argv[1])
        if len(sys.argv) == 2
        else DEFAULT_CSV_PATH
    )

    apply_review_decisions(
        csv_path
    )


if __name__ == "__main__":
    main()