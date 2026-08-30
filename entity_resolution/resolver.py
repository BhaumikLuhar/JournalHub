from __future__ import annotations

from typing import Any

from psycopg2 import sql

from entity_resolution.canonical import get_or_create_canonical_journal
from entity_resolution.issn_matcher import match_by_issn
from entity_resolution.matching import (
    _get_connection,
    accept_candidate_and_close_siblings,
    upsert_entity_match_candidate,
)
from entity_resolution.title_matcher import (
    match_by_exact_title,
    match_by_fuzzy_title,
)
from ingestion.common.normalization import (
    normalize_issn,
    normalize_title,
    normalized_matching_title,
)


FUZZY_THRESHOLD = 0.90
FUZZY_AUTO_ACCEPT_THRESHOLD = 0.97
FUZZY_TOP_N = 5

SYSTEM_REVIEWER = "system_auto"

SUPPORTED_SOURCE_TABLES = {
    "abdc_records",
    "abs_records",
    "repec_records",
    "ft50_records",
}


def _get_source_id(cursor, source_code: str) -> int:
    cursor.execute(
        """
        SELECT id
        FROM sources
        WHERE code = %s
        """,
        (source_code,),
    )

    row = cursor.fetchone()

    if row is None:
        raise ValueError(
            f"Unknown source code: {source_code!r}"
        )

    return int(row[0])


def _validate_source_table(table_name: str) -> None:
    if table_name not in SUPPORTED_SOURCE_TABLES:
        raise ValueError(
            f"Unsupported source record table: {table_name!r}"
        )


def _get_existing_decision(
    cursor,
    *,
    source_id: int,
    table_name: str,
    record_id: int,
):
    cursor.execute(
        """
        SELECT
            id,
            journal_id,
            match_method,
            confidence,
            decision
        FROM entity_match_decisions
        WHERE source_id = %s
          AND source_record_table = %s
          AND source_record_id = %s
        LIMIT 1
        """,
        (
            source_id,
            table_name,
            record_id,
        ),
    )

    return cursor.fetchone()


def _insert_decision(
    cursor,
    *,
    source_id: int,
    table_name: str,
    record_id: int,
    journal_id: int | None,
    match_method: str,
    confidence: float | None,
    decision: str,
) -> None:
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
            NOW()
        )
        ON CONFLICT (
            source_id,
            source_record_table,
            source_record_id
        )
        DO NOTHING
        """,
        (
            source_id,
            table_name,
            record_id,
            journal_id,
            match_method,
            confidence,
            decision,
            SYSTEM_REVIEWER,
        ),
    )


def _update_source_record(
    cursor,
    *,
    table_name: str,
    record_id: int,
    journal_id: int,
) -> None:
    """
    Update the source record's journal_id.

    table_name is validated against the explicit allow-list before
    being interpolated as an SQL identifier.
    """

    _validate_source_table(table_name)

    query = sql.SQL(
        """
        UPDATE {table_name}
        SET journal_id = %s
        WHERE id = %s
        """
    ).format(
        table_name=sql.Identifier(table_name)
    )

    cursor.execute(
        query,
        (
            journal_id,
            record_id,
        ),
    )

    if cursor.rowcount != 1:
        raise RuntimeError(
            f"Expected exactly one {table_name} record with id "
            f"{record_id}, but updated {cursor.rowcount} rows."
        )


def _insert_source_mapping(
    cursor,
    *,
    journal_id: int,
    source_id: int,
    table_name: str,
    record_id: int,
    match_method: str,
    match_score: float,
) -> None:
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
            %s
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
            table_name,
            record_id,
            match_method,
            match_score,
            "accepted",
        ),
    )


def _persist_candidate(
    cursor,
    *,
    source_id: int,
    table_name: str,
    record_id: int,
    candidate_journal_id: int,
    similarity: float,
    issn_match: bool,
    publisher_match: bool,
    match_method: str,
    rank: int,
) -> int:
    """
    Insert or merge one candidate using the existing shared helper,
    then retrieve its stable database ID.
    """

    upsert_entity_match_candidate(
        source_id,
        table_name,
        record_id,
        candidate_journal_id,
        similarity,
        issn_match,
        publisher_match,
        match_method,
        rank,
        conn=cursor.connection,
    )

    cursor.execute(
        """
        SELECT id
        FROM entity_match_candidates
        WHERE source_id = %s
          AND source_record_table = %s
          AND source_record_id = %s
          AND candidate_journal_id = %s
        """,
        (
            source_id,
            table_name,
            record_id,
            candidate_journal_id,
        ),
    )

    row = cursor.fetchone()

    if row is None:
        raise RuntimeError(
            "Candidate was not found after upsert."
        )

    return int(row[0])


def _accept_candidate(
    cursor,
    *,
    candidate_id: int,
    source_id: int,
    table_name: str,
    record_id: int,
    journal_id: int,
    match_method: str,
    confidence: float,
) -> None:
    """
    Accept a candidate, reject all pending siblings, update the source
    record, and create the accepted source mapping.
    """

    accept_candidate_and_close_siblings(
        candidate_id,
        reviewed_by=SYSTEM_REVIEWER,
        conn=cursor.connection,
    )

    _update_source_record(
        cursor,
        table_name=table_name,
        record_id=record_id,
        journal_id=journal_id,
    )

    _insert_source_mapping(
        cursor,
        journal_id=journal_id,
        source_id=source_id,
        table_name=table_name,
        record_id=record_id,
        match_method=match_method,
        match_score=confidence,
    )

    _insert_decision(
        cursor,
        source_id=source_id,
        table_name=table_name,
        record_id=record_id,
        journal_id=journal_id,
        match_method=match_method,
        confidence=confidence,
        decision="accepted",
    )


def _resolve_existing_decision(
    cursor,
    *,
    existing_decision,
    table_name: str,
    record_id: int,
) -> bool:
    """
    Apply an already accepted/manual decision.

    Returns True when resolution is complete.
    """

    decision = existing_decision[4]
    journal_id = existing_decision[1]

    if decision not in {
        "accepted",
        "manually_confirmed",
    }:
        return False

    if journal_id is None:
        raise RuntimeError(
            "Accepted/manual decision has NULL journal_id: "
            f"source_record_table={table_name!r}, "
            f"source_record_id={record_id}"
        )

    _update_source_record(
        cursor,
        table_name=table_name,
        record_id=record_id,
        journal_id=int(journal_id),
    )

    return True


def _unique_issn_values(
    issn: Any,
    issn_online: Any,
) -> list[str]:
    values: list[str] = []

    for raw_value in (issn, issn_online):
        normalized = normalize_issn(raw_value)

        if normalized and normalized not in values:
            values.append(normalized)

    return values


def resolve_record(
    source_code,
    table_name,
    record_id,
    journal_name,
    issn,
    issn_online,
    publisher=None,
    observed_year=None,
) -> None:
    """
    Resolve one source record against the canonical journals table.

    Resolution hierarchy:

        1. Existing accepted/manual decision
        2. ISSN / EISSN
        3. Exact normalized title
        4. Fuzzy normalized title
        5. New canonical journal

    Fuzzy outcomes:

        >= 0.97
            automatic acceptance

        0.90 <= score < 0.97
            leave candidates pending for manual review

        < 0.90 / no candidates
            create or reuse canonical journal

    The function owns its transaction when called directly.
    """

    _validate_source_table(table_name)

    connection = _get_connection()

    try:
        with connection:
            with connection.cursor() as cursor:
                source_id = _get_source_id(
                    cursor,
                    source_code,
                )

                # -----------------------------------------------------
                # 1. Existing decision
                # -----------------------------------------------------
                existing_decision = _get_existing_decision(
                    cursor,
                    source_id=source_id,
                    table_name=table_name,
                    record_id=record_id,
                )

                if existing_decision is not None:
                    _resolve_existing_decision(
                        cursor,
                        existing_decision=existing_decision,
                        table_name=table_name,
                        record_id=record_id,
                    )

                    return None

                # -----------------------------------------------------
                # Normalize title once.
                # -----------------------------------------------------
                candidate_title = normalize_title(
                    journal_name
                )

                matching_title = normalized_matching_title(
                    journal_name
                )

                if not matching_title:
                    raise ValueError(
                        f"Cannot resolve record {record_id}: "
                        "journal_name is empty after normalization."
                    )

                # -----------------------------------------------------
                # 2. Exact ISSN / EISSN
                # -----------------------------------------------------
                issn_values = _unique_issn_values(
                    issn,
                    issn_online,
                )

                issn_hits: list[int] = []

                for normalized_issn in issn_values:
                    journal_id = match_by_issn(
                        normalized_issn,
                        connection=connection,
                    )

                    if (
                        journal_id is not None
                        and journal_id not in issn_hits
                    ):
                        issn_hits.append(journal_id)

                # Exactly one canonical journal is identified.
                if len(issn_hits) == 1:
                    journal_id = issn_hits[0]

                    _insert_decision(
                        cursor,
                        source_id=source_id,
                        table_name=table_name,
                        record_id=record_id,
                        journal_id=journal_id,
                        match_method="exact_issn",
                        confidence=1.0,
                        decision="accepted",
                    )

                    _insert_source_mapping(
                        cursor,
                        journal_id=journal_id,
                        source_id=source_id,
                        table_name=table_name,
                        record_id=record_id,
                        match_method="exact_issn",
                        match_score=1.0,
                    )

                    _update_source_record(
                        cursor,
                        table_name=table_name,
                        record_id=record_id,
                        journal_id=journal_id,
                    )

                    return None

                # -----------------------------------------------------
                # 3. Exact normalized title
                # -----------------------------------------------------
                exact_matches = match_by_exact_title(
                    matching_title,
                    connection=connection,
                )

                if len(exact_matches) == 1:
                    journal_id = exact_matches[0]

                    _insert_decision(
                        cursor,
                        source_id=source_id,
                        table_name=table_name,
                        record_id=record_id,
                        journal_id=journal_id,
                        match_method="exact_title",
                        confidence=0.98,
                        decision="accepted",
                    )

                    _insert_source_mapping(
                        cursor,
                        journal_id=journal_id,
                        source_id=source_id,
                        table_name=table_name,
                        record_id=record_id,
                        match_method="exact_title",
                        match_score=0.98,
                    )

                    _update_source_record(
                        cursor,
                        table_name=table_name,
                        record_id=record_id,
                        journal_id=journal_id,
                    )

                    return None

                # -----------------------------------------------------
                # Ambiguous exact title:
                #
                # Store candidates, but DO NOT return.
                #
                # Similarity is deliberately 0.0 here. An ambiguous
                # exact-title match is evidence that the title exists
                # on multiple journals, not evidence that any specific
                # candidate has a 0.98 confidence.
                #
                # Fuzzy matching below can then merge stronger evidence
                # into the same candidate rows.
                # -----------------------------------------------------
                if len(exact_matches) > 1:
                    for rank, journal_id in enumerate(
                        exact_matches,
                        start=1,
                    ):
                        _persist_candidate(
                            cursor,
                            source_id=source_id,
                            table_name=table_name,
                            record_id=record_id,
                            candidate_journal_id=journal_id,
                            similarity=0.0,
                            issn_match=False,
                            publisher_match=False,
                            match_method="exact_title_ambiguous",
                            rank=rank,
                        )

                # -----------------------------------------------------
                # 4. Fuzzy title
                # -----------------------------------------------------
                fuzzy_candidates = match_by_fuzzy_title(
                    matching_title,
                    threshold=FUZZY_THRESHOLD,
                    top_n=FUZZY_TOP_N,
                    connection=connection,
                )

                persisted_fuzzy: list[
                    tuple[int, float, int]
                ] = []

                for rank, (
                    journal_id,
                    similarity,
                ) in enumerate(
                    fuzzy_candidates,
                    start=1,
                ):
                    candidate_id = _persist_candidate(
                        cursor,
                        source_id=source_id,
                        table_name=table_name,
                        record_id=record_id,
                        candidate_journal_id=journal_id,
                        similarity=similarity,
                        issn_match=False,
                        publisher_match=False,
                        match_method="fuzzy_title",
                        rank=rank,
                    )

                    persisted_fuzzy.append(
                        (
                            journal_id,
                            similarity,
                            candidate_id,
                        )
                    )

                # -----------------------------------------------------
                # Determine the best candidate AFTER evidence merging.
                #
                # The database is authoritative because an exact-title
                # candidate may have merged with fuzzy evidence.
                # -----------------------------------------------------
                cursor.execute(
                    """
                    SELECT
                        id,
                        candidate_journal_id,
                        similarity,
                        match_method,
                        rank_among_candidates
                    FROM entity_match_candidates
                    WHERE source_id = %s
                      AND source_record_table = %s
                      AND source_record_id = %s
                    ORDER BY
                        similarity DESC,
                        rank_among_candidates ASC,
                        id ASC
                    """,
                    (
                        source_id,
                        table_name,
                        record_id,
                    ),
                )

                merged_candidates = cursor.fetchall()

                if merged_candidates:
                    best_candidate = merged_candidates[0]

                    best_candidate_id = int(
                        best_candidate[0]
                    )
                    best_journal_id = int(
                        best_candidate[1]
                    )
                    best_similarity = float(
                        best_candidate[2]
                    )
                    best_match_method = str(
                        best_candidate[3]
                    )

                    # -------------------------------------------------
                    # >= 0.97: automatic acceptance.
                    # -------------------------------------------------
                    if (
                        best_similarity
                        >= FUZZY_AUTO_ACCEPT_THRESHOLD
                    ):
                        _accept_candidate(
                            cursor,
                            candidate_id=best_candidate_id,
                            source_id=source_id,
                            table_name=table_name,
                            record_id=record_id,
                            journal_id=best_journal_id,
                            match_method=best_match_method,
                            confidence=best_similarity,
                        )

                        return None

                    # -------------------------------------------------
                    # 0.90 <= score < 0.97:
                    #
                    # Do NOT insert a decision row.
                    # Candidates intentionally remain pending.
                    # Day 8 will review them.
                    # -------------------------------------------------
                    if best_similarity >= FUZZY_THRESHOLD:
                        return None

                # -----------------------------------------------------
                # 5. No sufficiently strong candidate.
                #
                # Create/reuse a canonical journal and immediately
                # register available ISSNs.
                # -----------------------------------------------------
                canonical_issns = [
                    value
                    for value in issn_values
                    if value is not None
                ]

                journal_id, _was_created = (
                    get_or_create_canonical_journal(
                        candidate_title=candidate_title,
                        matching_title=matching_title,
                        issn_list=canonical_issns,
                        source_id=source_id,
                        publisher=publisher,
                        observed_year=observed_year,
                        conn=connection,
                    )
                )

                _update_source_record(
                    cursor,
                    table_name=table_name,
                    record_id=record_id,
                    journal_id=journal_id,
                )

                _insert_source_mapping(
                    cursor,
                    journal_id=journal_id,
                    source_id=source_id,
                    table_name=table_name,
                    record_id=record_id,
                    match_method="new_journal",
                    match_score=1.0,
                )

                _insert_decision(
                    cursor,
                    source_id=source_id,
                    table_name=table_name,
                    record_id=record_id,
                    journal_id=journal_id,
                    match_method="new_journal",
                    confidence=1.0,
                    decision="new_journal",
                )

                return None

    finally:
        connection.close()