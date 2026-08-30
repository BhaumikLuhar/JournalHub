from __future__ import annotations

from entity_resolution.title_matcher import (
    DEFAULT_THRESHOLD,
    DEFAULT_TOP_N,
    TITLE_BLOCK_LENGTH,
    match_by_exact_title,
    match_by_fuzzy_title,
)
from ingestion.common.pipeline_helpers import _get_connection


def _get_duplicate_title() -> tuple[str, list[int]]:
    """
    Find a normalized title that intentionally belongs to multiple
    canonical journals.
    """
    connection = _get_connection()

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT normalized_title,
                       ARRAY_AGG(id ORDER BY id) AS journal_ids
                FROM journals
                GROUP BY normalized_title
                HAVING COUNT(*) > 1
                ORDER BY normalized_title
                LIMIT 1
                """
            )

            row = cursor.fetchone()

            if row is None:
                raise AssertionError(
                    "Expected at least one duplicate normalized title."
                )

            return (
                str(row[0]),
                [int(value) for value in row[1]],
            )

    finally:
        connection.close()


def _get_known_title() -> tuple[str, int]:
    """
    Return one canonical normalized title and its journal ID.
    """
    connection = _get_connection()

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT normalized_title, id
                FROM journals
                ORDER BY id
                LIMIT 1
                """
            )

            row = cursor.fetchone()

            if row is None:
                raise AssertionError(
                    "Expected at least one canonical journal."
                )

            return str(row[0]), int(row[1])

    finally:
        connection.close()


def test_exact_title_no_match() -> None:
    result = match_by_exact_title(
        "this title definitely does not exist in journalhub"
    )

    assert result == []


def test_exact_title_unique_match() -> None:
    normalized_title, expected_id = _get_known_title()

    result = match_by_exact_title(
        normalized_title
    )

    assert expected_id in result
    assert len(result) >= 1


def test_exact_title_returns_all_duplicates() -> None:
    normalized_title, expected_ids = _get_duplicate_title()

    result = match_by_exact_title(
        normalized_title
    )

    assert result == expected_ids
    assert len(result) > 1


def test_fuzzy_title_returns_ranked_candidates() -> None:
    normalized_title, expected_id = _get_known_title()

    result = match_by_fuzzy_title(
        normalized_title
    )

    assert result
    assert len(result) <= DEFAULT_TOP_N

    candidate_ids = [
        journal_id
        for journal_id, _score in result
    ]

    assert expected_id in candidate_ids

    scores = [
        score
        for _journal_id, score in result
    ]

    assert scores == sorted(
        scores,
        reverse=True,
    )

    assert all(
        DEFAULT_THRESHOLD <= score <= 1.0
        for score in scores
    )


def test_fuzzy_exact_title_scores_one() -> None:
    normalized_title, expected_id = _get_known_title()

    result = match_by_fuzzy_title(
        normalized_title
    )

    matching_scores = [
        score
        for journal_id, score in result
        if journal_id == expected_id
    ]

    assert matching_scores
    assert matching_scores[0] == 1.0


def test_fuzzy_top_n() -> None:
    normalized_title, _expected_id = _get_known_title()

    result = match_by_fuzzy_title(
        normalized_title,
        top_n=1,
    )

    assert len(result) <= 1


def test_fuzzy_threshold() -> None:
    normalized_title, _expected_id = _get_known_title()

    result = match_by_fuzzy_title(
        normalized_title,
        threshold=1.0,
    )

    assert all(
        score >= 1.0
        for _journal_id, score in result
    )


def test_empty_title() -> None:
    assert match_by_exact_title("") == []
    assert match_by_fuzzy_title("") == []


def test_invalid_threshold() -> None:
    try:
        match_by_fuzzy_title(
            "test title",
            threshold=1.1,
        )
    except ValueError:
        pass
    else:
        raise AssertionError(
            "Expected ValueError for threshold > 1.0"
        )


def test_invalid_top_n() -> None:
    try:
        match_by_fuzzy_title(
            "test title",
            top_n=0,
        )
    except ValueError:
        pass
    else:
        raise AssertionError(
            "Expected ValueError for top_n <= 0"
        )


def test_blocking_configuration() -> None:
    assert TITLE_BLOCK_LENGTH == 4


def main() -> None:
    print("Title matcher manual verification")
    print("=" * 60)

    print("Test 1: exact title no match")
    test_exact_title_no_match()
    print("PASS")

    print("Test 2: exact title unique match")
    test_exact_title_unique_match()
    print("PASS")

    print("Test 3: exact title returns all duplicates")
    test_exact_title_returns_all_duplicates()
    print("PASS")

    print("Test 4: fuzzy title ranked candidates")
    test_fuzzy_title_returns_ranked_candidates()
    print("PASS")

    print("Test 5: exact fuzzy match scores 1.0")
    test_fuzzy_exact_title_scores_one()
    print("PASS")

    print("Test 6: top-N limit")
    test_fuzzy_top_n()
    print("PASS")

    print("Test 7: threshold")
    test_fuzzy_threshold()
    print("PASS")

    print("Test 8: empty title")
    test_empty_title()
    print("PASS")

    print("Test 9: invalid threshold")
    test_invalid_threshold()
    print("PASS")

    print("Test 10: invalid top-N")
    test_invalid_top_n()
    print("PASS")

    print("Test 11: four-character blocking")
    test_blocking_configuration()
    print("PASS")

    print("=" * 60)
    print("ALL TITLE MATCHER ASSERTIONS PASSED")


if __name__ == "__main__":
    main()