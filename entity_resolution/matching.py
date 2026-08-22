import os

import psycopg2
from dotenv import load_dotenv


load_dotenv("database/.env")


def _get_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "journal_platform"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD"),
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
):
    conn = _get_connection()

    try:
        with conn:
            with conn.cursor() as cur:
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
                        match_method = CASE
                            WHEN entity_match_candidates.match_method
                                 = EXCLUDED.match_method
                                THEN entity_match_candidates.match_method
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
    finally:
        conn.close()


def accept_candidate_and_close_siblings(
    candidate_id,
    reviewed_by="system_auto",
):
    conn = _get_connection()

    try:
        with conn:
            with conn.cursor() as cur:

                cur.execute(
                    """
                    UPDATE entity_match_candidates
                    SET
                        review_status = 'accepted',
                        reviewed_by = %s,
                        reviewed_at = NOW()
                    WHERE id = %s
                    """,
                    (
                        reviewed_by,
                        candidate_id,
                    ),
                )

                if cur.rowcount != 1:
                    raise ValueError(
                        f"Candidate {candidate_id} was not found"
                    )

                cur.execute(
                    """
                    UPDATE entity_match_candidates
                    SET
                        review_status = 'rejected'
                    WHERE source_id = (
                        SELECT source_id
                        FROM entity_match_candidates
                        WHERE id = %s
                    )
                    AND source_record_table = (
                        SELECT source_record_table
                        FROM entity_match_candidates
                        WHERE id = %s
                    )
                    AND source_record_id = (
                        SELECT source_record_id
                        FROM entity_match_candidates
                        WHERE id = %s
                    )
                    AND id != %s
                    AND review_status = 'pending'
                    """,
                    (
                        candidate_id,
                        candidate_id,
                        candidate_id,
                        candidate_id,
                    ),
                )
    finally:
        conn.close()
