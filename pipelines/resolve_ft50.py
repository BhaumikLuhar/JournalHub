from __future__ import annotations

from entity_resolution.canonical import (
    get_or_create_canonical_journal,
)
from entity_resolution.matching import (
    _get_connection,
    upsert_entity_match_candidate,
)
from entity_resolution.resolver import (
    FUZZY_AUTO_ACCEPT_THRESHOLD,
    FUZZY_THRESHOLD,
    FUZZY_TOP_N,
    _accept_candidate,
    _get_existing_decision as _resolver_get_existing_decision,
    _insert_decision,
    _insert_source_mapping,
    _update_source_record,
)
from entity_resolution.title_matcher import (
    match_by_exact_title,
    match_by_fuzzy_title,
)
from ingestion.common.normalization import (
    normalize_title,
    normalized_matching_title,
)


SOURCE_CODE = "FT50"
TABLE_NAME = "ft50_records"

SYSTEM_REVIEWER = "system_auto"


def _get_source_id(cursor) -> int:
    """
    Return the database source ID for FT50.
    """

    cursor.execute(
        """
        SELECT id
        FROM sources
        WHERE code = %s
        """,
        (SOURCE_CODE,),
    )

    row = cursor.fetchone()

    if row is None:
        raise ValueError(
            f"Unknown source code: {SOURCE_CODE!r}"
        )

    return int(row[0])


def _load_unresolved_records() -> list[tuple]:
    """
    Load all currently unresolved FT50 records.

    FT50 has no ISSN and no publisher field, so resolution is
    title-only.
    """

    connection = _get_connection()

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    id,
                    journal_name,
                    ft50_year
                FROM ft50_records
                WHERE journal_id IS NULL
                ORDER BY id
                """
            )

            return cursor.fetchall()

    finally:
        connection.close()


def _persist_candidate(
    cursor,
    *,
    source_id: int,
    record_id: int,
    candidate_journal_id: int,
    similarity: float,
    match_method: str,
    rank: int,
) -> int:
    """
    Insert or merge one FT50 candidate using the shared candidate
    upsert helper, then retrieve its stable database ID.

    FT50 has no ISSN or publisher evidence, so both evidence flags
    are False.
    """

    upsert_entity_match_candidate(
        source_id,
        TABLE_NAME,
        record_id,
        candidate_journal_id,
        similarity,
        False,
        False,
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
            TABLE_NAME,
            record_id,
            candidate_journal_id,
        ),
    )

    row = cursor.fetchone()

    if row is None:
        raise RuntimeError(
            "FT50 candidate was not found after upsert."
        )

    return int(row[0])


def _get_existing_decision(
    cursor,
    *,
    source_id: int,
    record_id: int,
):
    """
    Retrieve any existing entity-resolution decision for an FT50 row.
    """

    return _resolver_get_existing_decision(
        cursor,
        source_id=source_id,
        table_name=TABLE_NAME,
        record_id=record_id,
    )


def _accept_exact_title(
    cursor,
    *,
    source_id: int,
    record_id: int,
    journal_id: int,
) -> None:
    """
    Accept an unambiguous exact-title match directly.

    This follows the existing resolver behavior: an unambiguous
    exact-title match does not require a candidate-review row.
    """

    _insert_decision(
        cursor,
        source_id=source_id,
        table_name=TABLE_NAME,
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
        table_name=TABLE_NAME,
        record_id=record_id,
        match_method="exact_title",
        match_score=0.98,
    )

    _update_source_record(
        cursor,
        table_name=TABLE_NAME,
        record_id=record_id,
        journal_id=journal_id,
    )


def _get_best_candidate(
    cursor,
    *,
    source_id: int,
    record_id: int,
):
    """
    Retrieve the strongest persisted FT50 candidate.

    Candidate rows are authoritative after evidence merging because
    upsert_entity_match_candidate() merges evidence into the database.
    """

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
            TABLE_NAME,
            record_id,
        ),
    )

    row = cursor.fetchone()

    if row is None:
        return None

    return (
        int(row[0]),
        int(row[1]),
        float(row[2]),
        str(row[3]),
        int(row[4]),
    )


def _resolve_existing_decision(
    cursor,
    *,
    existing_decision,
    record_id: int,
) -> bool:
    """
    Reapply an existing accepted/manual decision.

    Returns True when the record is already resolved.
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
            "Accepted/manual FT50 decision has NULL journal_id: "
            f"record_id={record_id}"
        )

    _update_source_record(
        cursor,
        table_name=TABLE_NAME,
        record_id=record_id,
        journal_id=int(journal_id),
    )

    return True


def _create_new_journal(
    cursor,
    *,
    source_id: int,
    record_id: int,
    journal_name: str,
    matching_title: str,
    ft50_year: int | None,
) -> None:
    """
    Create or reuse a canonical journal when no sufficiently strong
    entity-match candidate exists.

    FT50 provides no ISSN and no publisher, so those inputs are empty.
    """

    journal_id, _was_created = (
        get_or_create_canonical_journal(
            candidate_title=journal_name,
            matching_title=matching_title,
            issn_list=[],
            source_id=source_id,
            publisher=None,
            observed_year=ft50_year,
            conn=cursor.connection,
        )
    )

    _update_source_record(
        cursor,
        table_name=TABLE_NAME,
        record_id=record_id,
        journal_id=journal_id,
    )

    _insert_source_mapping(
        cursor,
        journal_id=journal_id,
        source_id=source_id,
        table_name=TABLE_NAME,
        record_id=record_id,
        match_method="new_journal",
        match_score=1.0,
    )

    _insert_decision(
        cursor,
        source_id=source_id,
        table_name=TABLE_NAME,
        record_id=record_id,
        journal_id=journal_id,
        match_method="new_journal",
        confidence=1.0,
        decision="new_journal",
    )


def _resolve_one_record(
    *,
    record_id: int,
    journal_name: str,
    ft50_year: int | None,
) -> None:
    """
    Resolve one FT50 record.

    Resolution:

        1. Existing accepted/manual decision
        2. Exact normalized title
        3. Fuzzy normalized title
        4. Automatic acceptance >= 0.97
        5. Pending manual review for 0.90 <= score < 0.97
        6. New canonical journal when no sufficiently strong
           candidate exists

    FT50 has no ISSN, so resolution is title-only.
    """

    connection = _get_connection()

    try:
        with connection:
            with connection.cursor() as cursor:

                source_id = _get_source_id(
                    cursor
                )

                # -------------------------------------------------
                # 1. Existing decision
                # -------------------------------------------------
                existing_decision = (
                    _get_existing_decision(
                        cursor,
                        source_id=source_id,
                        record_id=record_id,
                    )
                )

                if existing_decision is not None:
                    if _resolve_existing_decision(
                        cursor,
                        existing_decision=existing_decision,
                        record_id=record_id,
                    ):
                        return

                # -------------------------------------------------
                # 2. Normalize title.
                # -------------------------------------------------
                candidate_title = normalize_title(
                    journal_name
                )

                matching_title = (
                    normalized_matching_title(
                        journal_name
                    )
                )

                if not matching_title:
                    raise ValueError(
                        f"Cannot resolve FT50 record "
                        f"{record_id}: journal_name is empty "
                        "after normalization."
                    )

                # -------------------------------------------------
                # 3. Exact normalized title
                # -------------------------------------------------
                exact_matches = match_by_exact_title(
                    matching_title,
                    connection=connection,
                )

                if len(exact_matches) == 1:
                    _accept_exact_title(
                        cursor,
                        source_id=source_id,
                        record_id=record_id,
                        journal_id=int(
                            exact_matches[0]
                        ),
                    )

                    return

                # -------------------------------------------------
                # Ambiguous exact title.
                #
                # Persist all exact-title matches as candidates,
                # then continue to fuzzy matching.
                # -------------------------------------------------
                if len(exact_matches) > 1:
                    for rank, journal_id in enumerate(
                        exact_matches,
                        start=1,
                    ):
                        _persist_candidate(
                            cursor,
                            source_id=source_id,
                            record_id=record_id,
                            candidate_journal_id=int(
                                journal_id
                            ),
                            similarity=0.0,
                            match_method=(
                                "exact_title_ambiguous"
                            ),
                            rank=rank,
                        )

                # -------------------------------------------------
                # 4. Fuzzy title
                # -------------------------------------------------
                fuzzy_candidates = (
                    match_by_fuzzy_title(
                        matching_title,
                        threshold=FUZZY_THRESHOLD,
                        top_n=FUZZY_TOP_N,
                        connection=connection,
                    )
                )

                for rank, (
                    journal_id,
                    similarity,
                ) in enumerate(
                    fuzzy_candidates,
                    start=1,
                ):
                    _persist_candidate(
                        cursor,
                        source_id=source_id,
                        record_id=record_id,
                        candidate_journal_id=int(
                            journal_id
                        ),
                        similarity=float(
                            similarity
                        ),
                        match_method="fuzzy_title",
                        rank=rank,
                    )

                # -------------------------------------------------
                # 5. Evaluate merged candidate state.
                #
                # We query the database again after candidate
                # upserts because exact-title and fuzzy evidence
                # may have merged into the same candidate row.
                # -------------------------------------------------
                best_candidate = _get_best_candidate(
                    cursor,
                    source_id=source_id,
                    record_id=record_id,
                )

                if best_candidate is not None:
                    (
                        candidate_id,
                        candidate_journal_id,
                        similarity,
                        match_method,
                        _rank,
                    ) = best_candidate

                    # ---------------------------------------------
                    # >= 0.97: automatic acceptance.
                    # ---------------------------------------------
                    if (
                        similarity
                        >= FUZZY_AUTO_ACCEPT_THRESHOLD
                    ):
                        _accept_candidate(
                            cursor,
                            candidate_id=candidate_id,
                            source_id=source_id,
                            table_name=TABLE_NAME,
                            record_id=record_id,
                            journal_id=candidate_journal_id,
                            match_method=match_method,
                            confidence=similarity,
                        )

                        return

                    # ---------------------------------------------
                    # 0.90 <= score < 0.97:
                    #
                    # Leave candidates pending for Day 8 manual
                    # review. No decision is inserted here.
                    # ---------------------------------------------
                    if similarity >= FUZZY_THRESHOLD:
                        return

                # -------------------------------------------------
                # 6. No sufficiently strong candidate.
                #
                # FT50 has no ISSN to register.
                # -------------------------------------------------
                _create_new_journal(
                    cursor,
                    source_id=source_id,
                    record_id=record_id,
                    journal_name=candidate_title,
                    matching_title=matching_title,
                    ft50_year=ft50_year,
                )

    finally:
        connection.close()


def resolve_ft50() -> None:
    """
    Resolve all currently unresolved FT50 records.
    """

    records = _load_unresolved_records()

    total = len(records)

    print("FT50 entity resolution")
    print("=" * 60)
    print(
        f"Unresolved records loaded: {total}"
    )

    if total == 0:
        print("Nothing to resolve.")
        return

    processed = 0
    succeeded = 0
    failed = 0

    for (
        record_id,
        journal_name,
        ft50_year,
    ) in records:

        processed += 1

        try:
            _resolve_one_record(
                record_id=int(record_id),
                journal_name=str(
                    journal_name
                ),
                ft50_year=(
                    int(ft50_year)
                    if ft50_year is not None
                    else None
                ),
            )

            succeeded += 1

        except Exception as exc:
            failed += 1

            print(
                f"ERROR: record_id={record_id} "
                f"journal_name={journal_name!r} "
                f"ft50_year={ft50_year!r} "
                f"error={type(exc).__name__}: {exc}"
            )

        if processed % 10 == 0 or processed == total:
            print(
                f"Progress: {processed}/{total} "
                f"(succeeded={succeeded}, "
                f"failed={failed})"
            )

    print("=" * 60)
    print("FT50 entity resolution complete")
    print(f"Processed: {processed}")
    print(f"Succeeded: {succeeded}")
    print(f"Failed: {failed}")


def main() -> None:
    resolve_ft50()


if __name__ == "__main__":
    main()