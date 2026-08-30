from __future__ import annotations

from typing import Any

from rapidfuzz import fuzz

from ingestion.common.pipeline_helpers import _get_connection


DEFAULT_THRESHOLD = 0.90
DEFAULT_TOP_N = 5
TITLE_BLOCK_LENGTH = 4


def _get_connection_if_needed(connection: Any | None):
    """
    Return an existing caller-owned connection or open a new connection.

    The matcher supports an optional connection so resolver/batch callers
    can reuse one transaction without opening a connection per lookup.
    """
    if connection is not None:
        return connection, False

    return _get_connection(), True


def match_by_exact_title(
    normalized_title: str,
    *,
    connection: Any | None = None,
) -> list[int]:
    """
    Return all canonical journal IDs having the supplied normalized title.

    normalized_title must already use the project's matching normalization.

    The result is a list because normalized_title is intentionally not
    unique in the journals table.

    Returns:
        [] when no journal matches.
        [journal_id] when exactly one journal matches.
        [journal_id, ...] when multiple journals share the title.
    """
    if not normalized_title:
        return []

    connection, owns_connection = _get_connection_if_needed(connection)

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id
                FROM journals
                WHERE normalized_title = %s
                ORDER BY id
                """,
                (normalized_title,),
            )

            return [
                int(row[0])
                for row in cursor.fetchall()
            ]

    finally:
        if owns_connection:
            connection.close()


def _title_block(normalized_title: str) -> str:
    """
    Return the first four characters used for fuzzy candidate blocking.
    """
    return normalized_title[:TITLE_BLOCK_LENGTH]


def match_by_fuzzy_title(
    normalized_title: str,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    top_n: int = DEFAULT_TOP_N,
    connection: Any | None = None,
) -> list[tuple[int, float]]:
    """
    Return the top fuzzy title candidates above the threshold.

    Candidate generation is deliberately blocked by the first four
    characters of normalized_title before RapidFuzz scoring.

    The RapidFuzz scorer is token_sort_ratio.

    Args:
        normalized_title:
            Already-normalized title used for matching.

        threshold:
            Minimum similarity as a 0.0-1.0 value.
            Default: 0.90.

        top_n:
            Maximum number of candidates to return.
            Default: 5.

        connection:
            Optional caller-owned PostgreSQL connection.

    Returns:
        List of (journal_id, similarity) tuples sorted by:
            1. similarity descending
            2. journal_id ascending

        Similarity is represented as 0.0-1.0.
    """
    if not normalized_title:
        return []

    if not 0.0 <= threshold <= 1.0:
        raise ValueError(
            "threshold must be between 0.0 and 1.0"
        )

    if top_n <= 0:
        raise ValueError(
            "top_n must be greater than zero"
        )

    block = _title_block(normalized_title)

    # A title shorter than four characters still has a valid block.
    # PostgreSQL LIKE handles the prefix naturally.
    block_pattern = f"{block}%"

    connection, owns_connection = _get_connection_if_needed(connection)

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, normalized_title
                FROM journals
                WHERE normalized_title LIKE %s
                """,
                (block_pattern,),
            )

            candidates = cursor.fetchall()

        scored: list[tuple[int, float]] = []

        for journal_id, candidate_title in candidates:
            if not candidate_title:
                continue

            score = (
                fuzz.token_sort_ratio(
                    normalized_title,
                    candidate_title,
                )
                / 100.0
            )

            if score >= threshold:
                scored.append(
                    (
                        int(journal_id),
                        float(score),
                    )
                )

        scored.sort(
            key=lambda item: (
                -item[1],
                item[0],
            )
        )

        return scored[:top_n]

    finally:
        if owns_connection:
            connection.close()