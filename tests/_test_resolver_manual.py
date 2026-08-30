from __future__ import annotations

from typing import Any

from entity_resolution.matching import _get_connection
from entity_resolution.resolver import resolve_record
from ingestion.common.normalization import (
    normalize_issn,
    normalized_matching_title,
)


SOURCE_CODE = "ABDC"
TABLE_NAME = "abdc_records"
DATASET_ID = 54


def get_connection():
    return _get_connection()


def get_abdc_source_id(cursor) -> int:
    cursor.execute(
        """
        SELECT id
        FROM sources
        WHERE code = %s
        """,
        (SOURCE_CODE,),
    )

    row = cursor.fetchone()

    assert row is not None
    return int(row[0])


def insert_committed_abdc_record(
    *,
    journal_name: str,
    issn: str | None = None,
    issn_online: str | None = None,
    publisher: str | None = None,
    rating_year: int = 2025,
) -> int:
    """
    Create a temporary ABDC record and COMMIT it.

    The resolver opens its own connection, so the source record must
    already be visible in PostgreSQL before resolve_record() is called.
    """

    connection = get_connection()

    try:
        with connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO abdc_records (
                        journal_id,
                        dataset_id,
                        source_row_hash,
                        rating_year,
                        journal_name,
                        publisher,
                        issn,
                        issn_online,
                        for_scheme,
                        rating
                    )
                    VALUES (
                        NULL,
                        %s,
                        md5(
                            %s || clock_timestamp()::text
                        ),
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        'ANZSRC2020',
                        'A'
                    )
                    RETURNING id
                    """,
                    (
                        DATASET_ID,
                        journal_name,
                        rating_year,
                        journal_name,
                        publisher,
                        issn,
                        issn_online,
                    ),
                )

                record_id = cursor.fetchone()[0]

        # The context manager commits here.
        return int(record_id)

    finally:
        connection.close()


def cleanup_record(record_id: int) -> None:
    """
    Delete all resolution artifacts for a temporary ABDC record and then
    delete the source record itself.

    This cleanup runs after the resolver has committed its transaction.
    """

    connection = get_connection()

    try:
        with connection:
            with connection.cursor() as cursor:
                source_id = get_abdc_source_id(cursor)

                cursor.execute(
                    """
                    DELETE FROM journal_source_mapping
                    WHERE source_id = %s
                      AND source_record_table = %s
                      AND source_record_id = %s
                    """,
                    (
                        source_id,
                        TABLE_NAME,
                        record_id,
                    ),
                )

                cursor.execute(
                    """
                    DELETE FROM entity_match_decisions
                    WHERE source_id = %s
                      AND source_record_table = %s
                      AND source_record_id = %s
                    """,
                    (
                        source_id,
                        TABLE_NAME,
                        record_id,
                    ),
                )

                cursor.execute(
                    """
                    DELETE FROM entity_match_candidates
                    WHERE source_id = %s
                      AND source_record_table = %s
                      AND source_record_id = %s
                    """,
                    (
                        source_id,
                        TABLE_NAME,
                        record_id,
                    ),
                )

                cursor.execute(
                    """
                    DELETE FROM abdc_records
                    WHERE id = %s
                    """,
                    (record_id,),
                )

    finally:
        connection.close()


def test_existing_decision_path() -> None:
    connection = get_connection()

    try:
        with connection.cursor() as cursor:
            source_id = get_abdc_source_id(cursor)

            cursor.execute(
                """
                SELECT
                    d.source_record_id,
                    d.journal_id,
                    d.decision,
                    r.journal_name,
                    r.issn,
                    r.issn_online,
                    r.publisher,
                    r.rating_year
                FROM entity_match_decisions d
                JOIN abdc_records r
                  ON r.id = d.source_record_id
                WHERE d.source_id = %s
                  AND d.source_record_table = %s
                ORDER BY d.id
                LIMIT 1
                """,
                (
                    source_id,
                    TABLE_NAME,
                ),
            )

            row = cursor.fetchone()

            assert row is not None

            (
                record_id,
                journal_id,
                decision,
                journal_name,
                issn,
                issn_online,
                publisher,
                rating_year,
            ) = row

        # The decision already exists and the resolver should simply
        # reuse it. It opens its own connection.
        result = resolve_record(
            SOURCE_CODE,
            TABLE_NAME,
            record_id,
            journal_name,
            issn,
            issn_online,
            publisher,
            rating_year,
        )

        assert result is None

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT journal_id, decision
                FROM entity_match_decisions
                WHERE source_id = %s
                  AND source_record_table = %s
                  AND source_record_id = %s
                """,
                (
                    source_id,
                    TABLE_NAME,
                    record_id,
                ),
            )

            current = cursor.fetchone()

            assert current is not None
            assert current[0] == journal_id
            assert current[1] == decision

    finally:
        connection.close()


def test_exact_issn_path() -> None:
    connection = get_connection()

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    j.id,
                    j.canonical_title,
                    ji.normalized_value
                FROM journals j
                JOIN journal_identifiers ji
                  ON ji.journal_id = j.id
                WHERE ji.identifier_type IN ('ISSN', 'EISSN')
                ORDER BY j.id
                LIMIT 1
                """
            )

            row = cursor.fetchone()

            assert row is not None

            journal_id, title, identifier = row

    finally:
        connection.close()

    record_id = insert_committed_abdc_record(
        journal_name=title,
        issn=identifier,
        publisher="Resolver Test Publisher",
    )

    try:
        resolve_record(
            SOURCE_CODE,
            TABLE_NAME,
            record_id,
            title,
            identifier,
            None,
            "Resolver Test Publisher",
            2025,
        )

        connection = get_connection()

        try:
            with connection.cursor() as cursor:
                source_id = get_abdc_source_id(cursor)

                cursor.execute(
                    """
                    SELECT
                        journal_id,
                        decision,
                        match_method,
                        confidence
                    FROM entity_match_decisions
                    WHERE source_id = %s
                      AND source_record_table = %s
                      AND source_record_id = %s
                    """,
                    (
                        source_id,
                        TABLE_NAME,
                        record_id,
                    ),
                )

                decision = cursor.fetchone()

                assert decision is not None
                assert decision[0] == journal_id
                assert decision[1] == "accepted"
                assert decision[2] == "exact_issn"
                assert float(decision[3]) == 1.0

                cursor.execute(
                    """
                    SELECT journal_id
                    FROM abdc_records
                    WHERE id = %s
                    """,
                    (record_id,),
                )

                source_record = cursor.fetchone()

                assert source_record is not None
                assert source_record[0] == journal_id

        finally:
            connection.close()

    finally:
        cleanup_record(record_id)


def test_exact_title_path() -> None:
    connection = get_connection()

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    j.id,
                    j.canonical_title,
                    j.normalized_title
                FROM journals j
                WHERE j.normalized_title IS NOT NULL
                ORDER BY j.id
                LIMIT 1
                """
            )

            row = cursor.fetchone()

            assert row is not None

            journal_id, title, matching_title = row

            # Make sure the selected title really has exactly one
            # canonical match.
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM journals
                WHERE normalized_title = %s
                """,
                (matching_title,),
            )

            assert cursor.fetchone()[0] == 1

    finally:
        connection.close()

    record_id = insert_committed_abdc_record(
        journal_name=title,
        publisher="Resolver Test Publisher",
    )

    try:
        resolve_record(
            SOURCE_CODE,
            TABLE_NAME,
            record_id,
            title,
            None,
            None,
            "Resolver Test Publisher",
            2025,
        )

        connection = get_connection()

        try:
            with connection.cursor() as cursor:
                source_id = get_abdc_source_id(cursor)

                cursor.execute(
                    """
                    SELECT
                        journal_id,
                        decision,
                        match_method,
                        confidence
                    FROM entity_match_decisions
                    WHERE source_id = %s
                      AND source_record_table = %s
                      AND source_record_id = %s
                    """,
                    (
                        source_id,
                        TABLE_NAME,
                        record_id,
                    ),
                )

                decision = cursor.fetchone()

                assert decision is not None
                assert decision[0] == journal_id
                assert decision[1] == "accepted"
                assert decision[2] == "exact_title"
                assert float(decision[3]) == 0.98

        finally:
            connection.close()

    finally:
        cleanup_record(record_id)


def test_new_journal_path() -> None:
    """
    Force the resolver away from ISSN/title matching by using a unique
    title and a deliberately nonexistent ISSN.

    The resolver must create a canonical journal, register the ISSN,
    attach the source record, and create a new_journal decision.
    """

    unique_title = (
        "JournalHub Resolver New Journal Test "
        "9f31c5a7"
    )

    new_issn = "9876-5432"

    # Verify the test identifier does not already exist.
    connection = get_connection()

    try:
        with connection.cursor() as cursor:
            normalized = normalize_issn(new_issn)

            cursor.execute(
                """
                SELECT COUNT(*)
                FROM journal_identifiers
                WHERE identifier_type IN ('ISSN', 'EISSN')
                  AND normalized_value = %s
                """,
                (normalized,),
            )

            assert cursor.fetchone()[0] == 0

    finally:
        connection.close()

    record_id = insert_committed_abdc_record(
        journal_name=unique_title,
        issn=new_issn,
        publisher="Resolver New Journal Publisher",
    )

    created_journal_id = None

    try:
        resolve_record(
            SOURCE_CODE,
            TABLE_NAME,
            record_id,
            unique_title,
            new_issn,
            None,
            "Resolver New Journal Publisher",
            2025,
        )

        connection = get_connection()

        try:
            with connection.cursor() as cursor:
                source_id = get_abdc_source_id(cursor)

                cursor.execute(
                    """
                    SELECT
                        journal_id,
                        decision,
                        match_method
                    FROM entity_match_decisions
                    WHERE source_id = %s
                      AND source_record_table = %s
                      AND source_record_id = %s
                    """,
                    (
                        source_id,
                        TABLE_NAME,
                        record_id,
                    ),
                )

                decision = cursor.fetchone()

                assert decision is not None
                assert decision[1] == "new_journal"
                assert decision[2] == "new_journal"
                assert decision[0] is not None

                created_journal_id = int(decision[0])

                cursor.execute(
                    """
                    SELECT journal_id
                    FROM abdc_records
                    WHERE id = %s
                    """,
                    (record_id,),
                )

                source_record = cursor.fetchone()

                assert source_record is not None
                assert source_record[0] == created_journal_id

                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM journal_identifiers
                    WHERE journal_id = %s
                      AND identifier_type = 'ISSN'
                      AND normalized_value = %s
                    """,
                    (
                        created_journal_id,
                        normalize_issn(new_issn),
                    ),
                )

                assert cursor.fetchone()[0] == 1

        finally:
            connection.close()

    finally:
        cleanup_record(record_id)

        # The canonical journal created specifically for this test is
        # now safe to remove because the temporary source mapping and
        # source record have already been removed.
        if created_journal_id is not None:
            connection = get_connection()

            try:
                with connection:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            """
                            DELETE FROM journal_identifiers
                            WHERE journal_id = %s
                            """,
                            (created_journal_id,),
                        )

                        cursor.execute(
                            """
                            DELETE FROM journals
                            WHERE id = %s
                            """,
                            (created_journal_id,),
                        )

            finally:
                connection.close()


def test_candidate_sibling_invariant() -> None:
    connection = get_connection()

    try:
        with connection.cursor() as cursor:
            source_id = get_abdc_source_id(cursor)

            cursor.execute(
                """
                SELECT
                    source_record_id,
                    COUNT(*) FILTER (
                        WHERE review_status = 'accepted'
                    ) AS accepted_count,
                    COUNT(*) FILTER (
                        WHERE review_status = 'pending'
                    ) AS pending_count
                FROM entity_match_candidates
                WHERE source_id = %s
                  AND source_record_table = %s
                GROUP BY source_record_id
                HAVING
                    COUNT(*) FILTER (
                        WHERE review_status = 'accepted'
                    ) > 0
                    AND
                    COUNT(*) FILTER (
                        WHERE review_status = 'pending'
                    ) > 0
                """,
                (
                    source_id,
                    TABLE_NAME,
                ),
            )

            violations = cursor.fetchall()

            assert violations == [], (
                "Accepted candidates have pending siblings: "
                f"{violations}"
            )

    finally:
        connection.close()


def test_ambiguous_title_fuzzy_merge() -> None:
    """
    Verify the high-risk ambiguous-title -> fuzzy-merge path.

    The selected normalized title exists for multiple canonical journals.
    The resolver must:

    1. detect the ambiguous exact-title match,
    2. create one candidate per matching journal,
    3. run fuzzy matching,
    4. merge fuzzy evidence into the existing candidate rows,
    5. automatically accept the highest-confidence candidate when the
       resulting score reaches the acceptance threshold,
    6. close the sibling candidate(s),
    7. persist the accepted source mapping.

    The test uses a committed temporary ABDC record and removes all
    resolution artifacts afterward.
    """

    connection = get_connection()

    try:
        with connection.cursor() as cursor:
            source_id = get_abdc_source_id(cursor)

            cursor.execute(
                """
                SELECT
                    normalized_title,
                    COUNT(*) AS journal_count,
                    ARRAY_AGG(
                        id
                        ORDER BY id
                    ) AS journal_ids
                FROM journals
                WHERE normalized_title IS NOT NULL
                  AND btrim(normalized_title) <> ''
                GROUP BY normalized_title
                HAVING COUNT(*) > 1
                ORDER BY COUNT(*) ASC, normalized_title
                LIMIT 1
                """
            )

            row = cursor.fetchone()

            assert row is not None, (
                "No ambiguous normalized journal title exists; "
                "cannot execute the ambiguous-title resolver test."
            )

            matching_title = row[0]
            journal_count = int(row[1])
            journal_ids = [int(value) for value in row[2]]

            assert journal_count >= 2
            assert len(journal_ids) >= 2

            cursor.execute(
                """
                SELECT id, canonical_title
                FROM journals
                WHERE normalized_title = %s
                ORDER BY id
                """,
                (matching_title,),
            )

            canonical_rows = cursor.fetchall()

            assert len(canonical_rows) >= 2

            source_title = canonical_rows[0][1]

            # Confirm the current source does not already contain a
            # decision for the temporary test record namespace.
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM entity_match_decisions
                WHERE source_id = %s
                  AND source_record_table = %s
                """,
                (
                    source_id,
                    TABLE_NAME,
                ),
            )

            decisions_before = int(cursor.fetchone()[0])

    finally:
        connection.close()

    # Verify the exact-title matcher sees the intended ambiguity before
    # invoking the resolver.
    connection = get_connection()

    try:
        from entity_resolution.title_matcher import match_by_exact_title

        exact_matches = match_by_exact_title(
            matching_title,
            connection=connection,
        )

        assert len(exact_matches) >= 2

        for journal_id in journal_ids:
            assert journal_id in exact_matches

    finally:
        connection.close()

    record_id = insert_committed_abdc_record(
        journal_name=source_title,
        publisher="Resolver Ambiguous Title Test Publisher",
    )

    try:
        resolve_record(
            SOURCE_CODE,
            TABLE_NAME,
            record_id,
            source_title,
            None,
            None,
            "Resolver Ambiguous Title Test Publisher",
            2025,
        )

        connection = get_connection()

        try:
            with connection.cursor() as cursor:
                source_id = get_abdc_source_id(cursor)

                # -----------------------------------------------------
                # 1. Verify exactly one decision was created.
                # -----------------------------------------------------
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
                    """,
                    (
                        source_id,
                        TABLE_NAME,
                        record_id,
                    ),
                )

                decision = cursor.fetchone()

                assert decision is not None, (
                    "Expected an accepted decision for the "
                    "high-confidence fuzzy result."
                )

                (
                    decision_id,
                    accepted_journal_id,
                    decision_method,
                    decision_confidence,
                    decision_value,
                ) = decision

                assert decision_id is not None
                assert accepted_journal_id in journal_ids
                assert decision_value == "accepted"
                assert float(decision_confidence) >= 0.97

                # The method must preserve the fact that the original
                # title was ambiguous and fuzzy evidence was subsequently
                # incorporated.
                assert "exact_title_ambiguous" in decision_method
                assert "fuzzy_title" in decision_method

                # -----------------------------------------------------
                # 2. Verify candidate merge behavior.
                # -----------------------------------------------------
                cursor.execute(
                    """
                    SELECT
                        id,
                        candidate_journal_id,
                        similarity,
                        match_method,
                        rank_among_candidates,
                        review_status
                    FROM entity_match_candidates
                    WHERE source_id = %s
                      AND source_record_table = %s
                      AND source_record_id = %s
                    ORDER BY
                        rank_among_candidates ASC,
                        similarity DESC,
                        id ASC
                    """,
                    (
                        source_id,
                        TABLE_NAME,
                        record_id,
                    ),
                )

                candidates = cursor.fetchall()

                assert len(candidates) >= 2, (
                    "Expected at least two candidate rows for the "
                    "ambiguous normalized title."
                )

                candidate_journal_ids = {
                    int(candidate[1])
                    for candidate in candidates
                }

                # One candidate row per journal.
                assert len(candidate_journal_ids) == len(candidates)

                # All original ambiguous-title candidates must remain
                # represented.
                for journal_id in journal_ids:
                    assert journal_id in candidate_journal_ids

                # -----------------------------------------------------
                # 3. Verify the fuzzy evidence was merged rather than
                #    producing duplicate candidate rows.
                # -----------------------------------------------------
                assert any(
                    float(candidate[2]) >= 0.97
                    for candidate in candidates
                )

                accepted_candidates = [
                    candidate
                    for candidate in candidates
                    if candidate[5] == "accepted"
                ]

                rejected_candidates = [
                    candidate
                    for candidate in candidates
                    if candidate[5] == "rejected"
                ]

                assert len(accepted_candidates) == 1

                accepted_candidate = accepted_candidates[0]

                assert int(
                    accepted_candidate[1]
                ) == accepted_journal_id

                assert float(
                    accepted_candidate[2]
                ) == float(decision_confidence)

                assert (
                    "exact_title_ambiguous"
                    in accepted_candidate[3]
                )

                assert (
                    "fuzzy_title"
                    in accepted_candidate[3]
                )

                # Every sibling candidate must have been closed.
                assert len(rejected_candidates) == (
                    len(candidates) - 1
                )

                # No pending siblings may remain after acceptance.
                assert not any(
                    candidate[5] == "pending"
                    for candidate in candidates
                )

                # -----------------------------------------------------
                # 4. Verify source record resolution.
                # -----------------------------------------------------
                cursor.execute(
                    """
                    SELECT journal_id
                    FROM abdc_records
                    WHERE id = %s
                    """,
                    (record_id,),
                )

                source_record = cursor.fetchone()

                assert source_record is not None
                assert source_record[0] == accepted_journal_id

                # -----------------------------------------------------
                # 5. Verify exactly one source mapping was persisted.
                # -----------------------------------------------------
                cursor.execute(
                    """
                    SELECT
                        journal_id,
                        match_method,
                        match_score,
                        match_status
                    FROM journal_source_mapping
                    WHERE source_id = %s
                      AND source_record_table = %s
                      AND source_record_id = %s
                    """,
                    (
                        source_id,
                        TABLE_NAME,
                        record_id,
                    ),
                )

                mapping = cursor.fetchone()

                assert mapping is not None
                assert mapping[0] == accepted_journal_id
                assert mapping[1] == decision_method
                assert float(mapping[2]) == float(
                    decision_confidence
                )
                assert mapping[3] == "accepted"

                # -----------------------------------------------------
                # 6. Verify the test did not accidentally create more
                #    than one decision for this source record.
                # -----------------------------------------------------
                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM entity_match_decisions
                    WHERE source_id = %s
                      AND source_record_table = %s
                      AND source_record_id = %s
                    """,
                    (
                        source_id,
                        TABLE_NAME,
                        record_id,
                    ),
                )

                assert int(cursor.fetchone()[0]) == 1

        finally:
            connection.close()

    finally:
        cleanup_record(record_id)


def main() -> None:
    print("ABDC resolver Part-7 manual verification")
    print("=" * 60)

    print("Test 1: existing decision is not re-litigated")
    test_existing_decision_path()
    print("PASS")

    print("Test 2: exact ISSN resolution")
    test_exact_issn_path()
    print("PASS")

    print("Test 3: exact title resolution")
    test_exact_title_path()
    print("PASS")

    print("Test 4: new canonical journal + ISSN registration")
    test_new_journal_path()
    print("PASS")

    print("Test 6: ambiguous title -> fuzzy candidate merge")
    test_ambiguous_title_fuzzy_merge()
    print("PASS")

    print("Test 7: accepted candidates have no pending siblings")
    test_candidate_sibling_invariant()
    print("PASS")


if __name__ == "__main__":
    main()
