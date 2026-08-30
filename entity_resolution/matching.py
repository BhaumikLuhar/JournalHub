from __future__ import annotations

import os
from typing import Any

import psycopg2
from dotenv import load_dotenv


load_dotenv("database/.env")


def _get_connection():
    """Open a PostgreSQL connection using the project's database settings."""
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "journal_platform"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD"),
    )


def _upsert_entity_match_candidate_with_cursor(
    cur: Any,
    *,
    source_id,
    source_record_table,
    source_record_id,
    candidate_journal_id,
    similarity,
    issn_match,
    publisher_match,
    match_method,
    rank_among_candidates,
) -> None:
    """
    Insert or merge one entity-match candidate using the caller's cursor.

    The caller owns the transaction.
    """

    cur.execute(
        """
        INSERT INTO entity_match_candidates (
            source_id,
            source_record_table,
            source_record_id,
            candidate_journal_id,
            similarity,
            issn_match,
            publisher_match,
            match_method,
            rank_among_candidates
        )
        VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s
        )
        ON CONFLICT (
            source_id,
            source_record_table,
            source_record_id,
            candidate_journal_id
        )
        DO UPDATE SET
            similarity = GREATEST(
                entity_match_candidates.similarity,
                EXCLUDED.similarity
            ),
            issn_match = CASE
                WHEN EXCLUDED.issn_match IS TRUE
                    THEN TRUE
                ELSE entity_match_candidates.issn_match
            END,
            publisher_match = CASE
                WHEN EXCLUDED.publisher_match IS TRUE
                    THEN TRUE
                ELSE entity_match_candidates.publisher_match
            END,
            match_method = CASE
                WHEN entity_match_candidates.match_method
                     = EXCLUDED.match_method
                    THEN entity_match_candidates.match_method
                WHEN entity_match_candidates.match_method IS NULL
                    THEN EXCLUDED.match_method
                WHEN EXCLUDED.match_method IS NULL
                    THEN entity_match_candidates.match_method
                WHEN POSITION(
                    EXCLUDED.match_method
                    IN entity_match_candidates.match_method
                ) > 0
                    THEN entity_match_candidates.match_method
                WHEN POSITION(
                    entity_match_candidates.match_method
                    IN EXCLUDED.match_method
                ) > 0
                    THEN EXCLUDED.match_method
                ELSE
                    entity_match_candidates.match_method
                    || '+'
                    || EXCLUDED.match_method
            END,
            rank_among_candidates = LEAST(
                entity_match_candidates.rank_among_candidates,
                EXCLUDED.rank_among_candidates
            )
        WHERE entity_match_candidates.review_status = 'pending'
        """,
        (
            source_id,
            source_record_table,
            source_record_id,
            candidate_journal_id,
            similarity,
            issn_match,
            publisher_match,
            match_method,
            rank_among_candidates,
        ),
    )


def upsert_entity_match_candidate(
    source_id,
    source_record_table,
    source_record_id,
    candidate_journal_id,
    similarity,
    issn_match,
    publisher_match,
    match_method,
    rank_among_candidates,
    conn=None,
):
    """
    Insert or merge an entity-match candidate.

    Transaction behavior:

    - conn=None:
        Preserve the original standalone behavior. This function creates
        and owns its own connection and transaction.

    - conn=<existing connection>:
        Use the caller's connection. The caller owns commit, rollback,
        and connection lifetime.

    The caller-owned mode is used by the Day-6 resolver so candidate
    operations participate in the same transaction as the final
    resolution decision.
    """

    owns_connection = conn is None

    if owns_connection:
        conn = _get_connection()

    try:
        if owns_connection:
            with conn:
                with conn.cursor() as cur:
                    _upsert_entity_match_candidate_with_cursor(
                        cur,
                        source_id=source_id,
                        source_record_table=source_record_table,
                        source_record_id=source_record_id,
                        candidate_journal_id=candidate_journal_id,
                        similarity=similarity,
                        issn_match=issn_match,
                        publisher_match=publisher_match,
                        match_method=match_method,
                        rank_among_candidates=rank_among_candidates,
                    )
        else:
            with conn.cursor() as cur:
                _upsert_entity_match_candidate_with_cursor(
                    cur,
                    source_id=source_id,
                    source_record_table=source_record_table,
                    source_record_id=source_record_id,
                    candidate_journal_id=candidate_journal_id,
                    similarity=similarity,
                    issn_match=issn_match,
                    publisher_match=publisher_match,
                    match_method=match_method,
                    rank_among_candidates=rank_among_candidates,
                )

    finally:
        if owns_connection:
            conn.close()


def _accept_candidate_and_close_siblings_with_cursor(
    cur: Any,
    *,
    candidate_id,
    reviewed_by="system_auto",
) -> None:
    """
    Accept one candidate and reject all pending siblings.

    The caller owns the transaction.
    """

    cur.execute(
        """
        SELECT
            source_id,
            source_record_table,
            source_record_id,
            review_status
        FROM entity_match_candidates
        WHERE id = %s
        FOR UPDATE
        """,
        (candidate_id,),
    )

    candidate = cur.fetchone()

    if candidate is None:
        raise ValueError(
            f"Candidate {candidate_id} was not found"
        )

    source_id = candidate[0]
    source_record_table = candidate[1]
    source_record_id = candidate[2]
    review_status = candidate[3]

    if review_status == "accepted":
        # Already accepted. This makes repeated resolver calls safely
        # idempotent instead of creating a second acceptance transition.
        return

    if review_status != "pending":
        raise ValueError(
            f"Candidate {candidate_id} has review_status="
            f"{review_status!r} and cannot be accepted"
        )

    cur.execute(
        """
        UPDATE entity_match_candidates
        SET
            review_status = 'accepted',
            reviewed_by = %s,
            reviewed_at = NOW()
        WHERE id = %s
          AND review_status = 'pending'
        """,
        (
            reviewed_by,
            candidate_id,
        ),
    )

    if cur.rowcount != 1:
        raise ValueError(
            f"Candidate {candidate_id} could not be accepted"
        )

    cur.execute(
        """
        UPDATE entity_match_candidates
        SET
            review_status = 'rejected'
        WHERE source_id = %s
          AND source_record_table = %s
          AND source_record_id = %s
          AND id != %s
          AND review_status = 'pending'
        """,
        (
            source_id,
            source_record_table,
            source_record_id,
            candidate_id,
        ),
    )


def accept_candidate_and_close_siblings(
    candidate_id,
    reviewed_by="system_auto",
    conn=None,
):
    """
    Accept a candidate and reject all pending siblings.

    Transaction behavior:

    - conn=None:
        Preserve standalone behavior by creating an independent
        connection and transaction.

    - conn=<existing connection>:
        Use the caller's connection without committing, rolling back,
        or closing it.
    """

    owns_connection = conn is None

    if owns_connection:
        conn = _get_connection()

    try:
        if owns_connection:
            with conn:
                with conn.cursor() as cur:
                    _accept_candidate_and_close_siblings_with_cursor(
                        cur,
                        candidate_id=candidate_id,
                        reviewed_by=reviewed_by,
                    )
        else:
            with conn.cursor() as cur:
                _accept_candidate_and_close_siblings_with_cursor(
                    cur,
                    candidate_id=candidate_id,
                    reviewed_by=reviewed_by,
                )

    finally:
        if owns_connection:
            conn.close()