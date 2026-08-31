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
    normalized_matching_title,
)


SOURCE_CODE = "REPEC"
TABLE_NAME = "repec_records"

SYSTEM_REVIEWER = "system_auto"


def _get_source_id(cursor) -> int:
    """
    Return the database source ID for RePEc.
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
    Load all currently unresolved RePEc records.

    RePEc has no ISSN field, so resolution is title-only.

    Both the publisher-stripped title and the original title are loaded
    because the Day 7 resolver may need to compare both representations.
    """

    connection = _get_connection()

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    id,
                    journal_name_raw,
                    journal_name_clean,
                    publisher_from_name,
                    publisher_split_confidence
                FROM repec_records
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
    Insert or merge one RePEc candidate using the shared candidate
    upsert helper, then retrieve its stable database ID.

    RePEc has no ISSN evidence and no source-level publisher-match
    evidence, so both flags are False.
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
            "Candidate was not found after upsert."
        )

    return int(row[0])


def _get_existing_decision(
    cursor,
    *,
    source_id: int,
    record_id: int,
):
    """
    Retrieve any existing entity-resolution decision for a RePEc row.
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

    This deliberately does not create an entity_match_candidates row.
    It follows the same behavior as resolver.py's exact-title path.
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


def _collect_title_candidates(
    cursor,
    *,
    source_id: int,
    record_id: int,
    title: str,
    title_label: str,
) -> tuple[bool, int | None]:
    """
    Run exact-title and fuzzy-title matching for one title
    representation.

    Returns:
        (exactly_one_exact_match, exact_journal_id)

    Exact-title behavior mirrors resolver.py:

        0 matches
            continue to fuzzy matching

        1 match
            caller may accept directly

        >1 matches
            persist exact_title_ambiguous candidates and continue
            to fuzzy matching so evidence can merge
    """

    matching_title = normalized_matching_title(
        title
    )

    if not matching_title:
        return False, None

    # -------------------------------------------------------------
    # Exact normalized title
    # -------------------------------------------------------------
    exact_matches = match_by_exact_title(
        matching_title,
        connection=cursor.connection,
    )

    if len(exact_matches) == 1:
        return True, int(exact_matches[0])

    if len(exact_matches) > 1:
        for rank, journal_id in enumerate(
            exact_matches,
            start=1,
        ):
            _persist_candidate(
                cursor,
                source_id=source_id,
                record_id=record_id,
                candidate_journal_id=int(journal_id),
                similarity=0.0,
                match_method=(
                    "exact_title_ambiguous"
                    if title_label == "clean"
                    else "exact_title_ambiguous_raw"
                ),
                rank=rank,
            )

    # -------------------------------------------------------------
    # Fuzzy normalized title
    # -------------------------------------------------------------
    fuzzy_candidates = match_by_fuzzy_title(
        matching_title,
        threshold=FUZZY_THRESHOLD,
        top_n=FUZZY_TOP_N,
        connection=cursor.connection,
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
            candidate_journal_id=int(journal_id),
            similarity=float(similarity),
            match_method=(
                "fuzzy_title"
                if title_label == "clean"
                else "fuzzy_title_raw"
            ),
            rank=rank,
        )

    return False, None


def _get_best_candidate(
    cursor,
    *,
    source_id: int,
    record_id: int,
):
    """
    Retrieve the strongest currently persisted candidate after all
    title evidence has been merged.

    Candidate merging is performed by the shared upsert helper.
    The database therefore represents the authoritative merged state.
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
            "Accepted/manual RePEc decision has NULL journal_id: "
            f"record_id={record_id}"
        )

    _update_source_record(
        cursor,
        table_name=TABLE_NAME,
        record_id=record_id,
        journal_id=int(journal_id),
    )

    return True


def _resolve_one_record(
    *,
    record_id: int,
    journal_name_raw: str,
    journal_name_clean: str,
    publisher: str | None,
    publisher_split_confidence: str | None,
) -> None:
    """
    Resolve one RePEc record.

    RePEc has no identifier, so ISSN matching is deliberately skipped.

    Resolution uses:

        1. Existing accepted/manual decision
        2. Clean-title exact matching
        3. Clean-title fuzzy matching
        4. Raw-title exact/fuzzy fallback when the publisher split
           confidence is low/none
        5. Best merged candidate
        6. New canonical journal when no sufficiently strong candidate
           exists
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
                # 2. Build matching-only title representations.
                # -------------------------------------------------
                clean_matching_title = (
                    normalized_matching_title(
                        journal_name_clean
                    )
                )

                raw_matching_title = (
                    normalized_matching_title(
                        journal_name_raw
                    )
                )

                if not clean_matching_title:
                    raise ValueError(
                        f"Cannot resolve RePEc record "
                        f"{record_id}: journal_name_clean is "
                        "empty after normalization."
                    )

                # -------------------------------------------------
                # 3. Clean-title exact matching.
                #
                # Exactly one match is accepted directly, exactly
                # like resolver.py.
                #
                # Ambiguous exact matches are persisted as candidates
                # and fuzzy matching continues.
                # -------------------------------------------------
                (
                    clean_exact_unique,
                    clean_exact_journal_id,
                ) = _collect_title_candidates(
                    cursor,
                    source_id=source_id,
                    record_id=record_id,
                    title=journal_name_clean,
                    title_label="clean",
                )

                if (
                    clean_exact_unique
                    and clean_exact_journal_id is not None
                ):
                    _accept_exact_title(
                        cursor,
                        source_id=source_id,
                        record_id=record_id,
                        journal_id=clean_exact_journal_id,
                    )

                    return

                # -------------------------------------------------
                # 4. Raw-title fallback.
                #
                # The Day 7 requirement specifically protects
                # against unsafe/declined publisher splitting.
                #
                # For low/none confidence the splitter returns the
                # original title as journal_name_clean, so this is
                # normally the same matching representation. We still
                # execute the path explicitly so the confidence flag
                # controls the fallback behavior.
                # -------------------------------------------------
                if publisher_split_confidence in {
                    "low",
                    "none",
                }:
                    if (
                        raw_matching_title
                        and raw_matching_title
                        != clean_matching_title
                    ):
                        (
                            raw_exact_unique,
                            raw_exact_journal_id,
                        ) = _collect_title_candidates(
                            cursor,
                            source_id=source_id,
                            record_id=record_id,
                            title=journal_name_raw,
                            title_label="raw",
                        )

                        if (
                            raw_exact_unique
                            and raw_exact_journal_id is not None
                        ):
                            _accept_exact_title(
                                cursor,
                                source_id=source_id,
                                record_id=record_id,
                                journal_id=raw_exact_journal_id,
                            )

                            return

                # -------------------------------------------------
                # 5. Evaluate merged candidate state.
                #
                # This is necessary after fuzzy matching and any
                # ambiguous exact-title evidence.
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
                    # Leave candidates pending for Day 8.
                    # No decision row is created.
                    # ---------------------------------------------
                    if similarity >= FUZZY_THRESHOLD:
                        return

                # -------------------------------------------------
                # 6. No sufficiently strong candidate.
                #
                # RePEc has no ISSNs, so there is nothing to register
                # through issn_list.
                # -------------------------------------------------
                journal_id, _was_created = (
                    get_or_create_canonical_journal(
                        candidate_title=journal_name_clean,
                        matching_title=clean_matching_title,
                        issn_list=[],
                        source_id=source_id,
                        publisher=publisher,
                        observed_year=None,
                        conn=connection,
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

    finally:
        connection.close()


def resolve_repec() -> None:
    """
    Resolve all currently unresolved RePEc records.
    """

    records = _load_unresolved_records()

    total = len(records)

    print("RePEc entity resolution")
    print("=" * 60)
    print(f"Unresolved records loaded: {total}")

    if total == 0:
        print("Nothing to resolve.")
        return

    processed = 0
    succeeded = 0
    failed = 0

    for (
        record_id,
        journal_name_raw,
        journal_name_clean,
        publisher,
        publisher_split_confidence,
    ) in records:

        processed += 1

        try:
            _resolve_one_record(
                record_id=int(record_id),
                journal_name_raw=str(
                    journal_name_raw
                ),
                journal_name_clean=str(
                    journal_name_clean
                ),
                publisher=publisher,
                publisher_split_confidence=(
                    publisher_split_confidence
                ),
            )

            succeeded += 1

        except Exception as exc:
            failed += 1

            print(
                f"ERROR: record_id={record_id} "
                f"journal_name={journal_name_raw!r} "
                f"confidence="
                f"{publisher_split_confidence!r} "
                f"error={type(exc).__name__}: {exc}"
            )

        if processed % 250 == 0 or processed == total:
            print(
                f"Progress: {processed}/{total} "
                f"(succeeded={succeeded}, failed={failed})"
            )

    print("=" * 60)
    print("RePEc entity resolution complete")
    print(f"Processed: {processed}")
    print(f"Succeeded: {succeeded}")
    print(f"Failed: {failed}")


def main() -> None:
    resolve_repec()


if __name__ == "__main__":
    main()