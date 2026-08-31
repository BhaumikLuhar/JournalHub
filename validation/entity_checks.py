from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path
from typing import Any

from ingestion.common.pipeline_helpers import _get_connection
from entity_resolution.title_matcher import match_by_fuzzy_title
from ingestion.common.normalization import normalized_matching_title


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ENTITY_FLAG_REPORT = Path("reports") / "entity_check_flags.csv"
DANGLING_REPORT = Path("reports") / "dangling_source_references.csv"

# Deterministic sample sizes.
ACCEPTED_MATCH_SAMPLE_SIZE = 500
SOURCE_MAPPING_SAMPLE_SIZE = 500

# Flag accepted matches when the recomputed fuzzy score has fallen by
# at least this absolute amount from the score stored at acceptance time.
SCORE_DROP_THRESHOLD = Decimal("0.050")


# These are the only source tables allowed in journal_source_mapping.
SOURCE_TABLES = {
    "scimago_records",
    "abdc_records",
    "abs_records",
    "repec_records",
    "ft50_records",
}


# Current title column for each normalized source table.
SOURCE_TITLE_COLUMNS = {
    "scimago_records": "title",
    "abdc_records": "journal_name",
    "abs_records": "journal_name",
    "repec_records": "journal_name_clean",
    "ft50_records": "journal_name",
}


ENTITY_FLAG_FIELDS = [
    "source_record_table",
    "source_record_id",
    "journal_id",
    "accepted_score",
    "recomputed_score",
    "score_drop",
    "accepted_at",
    "reason",
]


DANGLING_FIELDS = [
    "source_id",
    "source_record_table",
    "source_record_id",
    "journal_id",
    "reason",
]


# ---------------------------------------------------------------------------
# Report helpers
# ---------------------------------------------------------------------------

def _write_csv(
    path: Path,
    fieldnames: list[str],
    rows: list[dict[str, Any]],
) -> None:
    """
    Regenerate a validation report from the current database state.
    """

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )

        writer.writeheader()

        for row in rows:
            writer.writerow(row)


# ---------------------------------------------------------------------------
# Accepted fuzzy-match validation
# ---------------------------------------------------------------------------

def _fetch_accepted_match_sample(cursor):
    """
    Fetch a deterministic sample of accepted fuzzy-title candidates.

    Exact-title / ISSN accepted matches do not necessarily have a
    meaningful fuzzy score, so this check is limited to accepted
    candidates whose match_method contains fuzzy_title.

    The historical acceptance score comes from
    entity_match_candidates.similarity.
    """

    cursor.execute(
        """
        SELECT
            c.source_record_table,
            c.source_record_id,
            c.candidate_journal_id,
            c.similarity,
            c.reviewed_at
        FROM entity_match_candidates AS c
        WHERE c.review_status = 'accepted'
          AND c.similarity IS NOT NULL
          AND c.match_method LIKE 'fuzzy_title%%'
        ORDER BY
            c.source_record_table,
            c.source_record_id,
            c.candidate_journal_id,
            c.id
        LIMIT %s
        """,
        (ACCEPTED_MATCH_SAMPLE_SIZE,),
    )

    return cursor.fetchall()

def _fetch_source_title(
    cursor,
    source_record_table: str,
    source_record_id: int,
) -> str | None:
    """
    Fetch the current title from the appropriate literal source table.

    Table names are never accepted directly from arbitrary input;
    they must first pass the explicit allow-list above.
    """

    if source_record_table not in SOURCE_TABLES:
        raise ValueError(
            f"Unsupported source table: {source_record_table!r}"
        )

    title_column = SOURCE_TITLE_COLUMNS[source_record_table]

    query = f"""
        SELECT {title_column}
        FROM {source_record_table}
        WHERE id = %s
    """

    cursor.execute(
        query,
        (source_record_id,),
    )

    row = cursor.fetchone()

    if row is None:
        return None

    return row[0]


def _fetch_journal_matching_title(
    cursor,
    journal_id: int,
) -> str | None:
    """
    Fetch the canonical journal title and apply the project's current
    matching-title normalization before fuzzy comparison.
    """

    cursor.execute(
        """
        SELECT canonical_title
        FROM journals
        WHERE id = %s
        """,
        (journal_id,),
    )

    row = cursor.fetchone()

    if row is None:
        return None

    return normalized_matching_title(row[0])


def _recompute_fuzzy_score(
    cursor,
    source_record_table: str,
    source_record_id: int,
    journal_id: int,
) -> float | None:
    """
    Recompute the current fuzzy-title similarity for an accepted match.

    This deliberately uses the same match_by_fuzzy_title() function
    used by the production resolver. We do not create a second fuzzy
    algorithm inside the validation suite.
    """

    source_title = _fetch_source_title(
        cursor,
        source_record_table,
        source_record_id,
    )

    if not source_title:
        return None

    matching_title = normalized_matching_title(source_title)

    if not matching_title:
        return None

    candidates = match_by_fuzzy_title(
        matching_title,
        threshold=0.0,
        top_n=1,
        connection=cursor.connection,
    )

    for candidate_journal_id, similarity in candidates:
        if int(candidate_journal_id) == int(journal_id):
            return float(similarity)

    return None


def _check_accepted_match_scores(
    cursor,
) -> list[dict[str, Any]]:
    """
    Recompute a deterministic sample of accepted fuzzy matches.

    A material score deterioration is reported but never auto-fixed.
    """

    sample = _fetch_accepted_match_sample(cursor)

    flags: list[dict[str, Any]] = []

    checked = 0
    unable_to_recompute = 0

    for (
        source_record_table,
        source_record_id,
        journal_id,
        accepted_score,
        accepted_at,
    ) in sample:

        recomputed_score = _recompute_fuzzy_score(
            cursor,
            source_record_table,
            int(source_record_id),
            int(journal_id),
        )

        if recomputed_score is None:
            unable_to_recompute += 1
            continue

        checked += 1

        accepted_decimal = Decimal(str(accepted_score))
        recomputed_decimal = Decimal(str(recomputed_score))
        score_drop = accepted_decimal - recomputed_decimal

        if score_drop >= SCORE_DROP_THRESHOLD:
            flags.append(
                {
                    "source_record_table": source_record_table,
                    "source_record_id": source_record_id,
                    "journal_id": journal_id,
                    "accepted_score": accepted_decimal,
                    "recomputed_score": recomputed_decimal,
                    "score_drop": score_drop,
                    "accepted_at": accepted_at,
                    "reason": (
                        "Current fuzzy score is at least "
                        f"{SCORE_DROP_THRESHOLD} below the "
                        "score recorded at acceptance time."
                    ),
                }
            )

    print(
        f"Accepted fuzzy-match sample selected: {len(sample)}"
    )
    print(
        f"Accepted fuzzy matches successfully recomputed: {checked}"
    )
    print(
        f"Accepted fuzzy matches not recomputable: "
        f"{unable_to_recompute}"
    )
    print(
        f"Accepted fuzzy matches flagged: {len(flags)}"
    )

    return flags


# ---------------------------------------------------------------------------
# Polymorphic source-reference validation
# ---------------------------------------------------------------------------

def _fetch_source_mapping_sample(cursor):
    """
    Fetch a deterministic sample of journal_source_mapping rows.
    """

    cursor.execute(
        """
        SELECT
            source_id,
            source_record_table,
            source_record_id,
            journal_id
        FROM journal_source_mapping
        ORDER BY
            source_record_table,
            source_record_id,
            journal_id
        LIMIT %s
        """,
        (SOURCE_MAPPING_SAMPLE_SIZE,),
    )

    return cursor.fetchall()


def _source_record_exists(
    cursor,
    source_record_table: str,
    source_record_id: int,
) -> bool:
    """
    Verify that a polymorphic source reference points to an actual
    source-table row.
    """

    if source_record_table not in SOURCE_TABLES:
        raise ValueError(
            f"Unsupported source table in journal_source_mapping: "
            f"{source_record_table!r}"
        )

    cursor.execute(
        f"""
        SELECT 1
        FROM {source_record_table}
        WHERE id = %s
        LIMIT 1
        """,
        (source_record_id,),
    )

    return cursor.fetchone() is not None


def _check_dangling_source_references(
    cursor,
) -> list[dict[str, Any]]:
    """
    Validate the sampled polymorphic source references.
    """

    sample = _fetch_source_mapping_sample(cursor)

    dangling: list[dict[str, Any]] = []

    checked = 0

    for (
        source_id,
        source_record_table,
        source_record_id,
        journal_id,
    ) in sample:

        checked += 1

        if not _source_record_exists(
            cursor,
            source_record_table,
            int(source_record_id),
        ):
            dangling.append(
                {
                    "source_id": source_id,
                    "source_record_table": source_record_table,
                    "source_record_id": source_record_id,
                    "journal_id": journal_id,
                    "reason": (
                        "journal_source_mapping references a source "
                        "record ID that does not exist in the literal "
                        "source_record_table."
                    ),
                }
            )

    print(
        f"journal_source_mapping sample selected: {len(sample)}"
    )
    print(
        f"Source references checked: {checked}"
    )
    print(
        f"Dangling references found: {len(dangling)}"
    )

    return dangling


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_entity_checks() -> None:
    """
    Execute the Day 9 entity-resolution validation suite.

    The entire validation is read-only.
    """

    print("=" * 72)
    print("JournalHub Day 9 — Entity Resolution Checks")
    print("=" * 72)

    connection = _get_connection()

    try:
        with connection:
            with connection.cursor() as cursor:

                print()
                print(
                    "[1/2] Recomputing accepted fuzzy-match scores..."
                )

                entity_flags = _check_accepted_match_scores(
                    cursor
                )

                print()
                print(
                    "[2/2] Checking polymorphic source references..."
                )

                dangling_references = (
                    _check_dangling_source_references(
                        cursor
                    )
                )

    finally:
        connection.close()

    _write_csv(
        ENTITY_FLAG_REPORT,
        ENTITY_FLAG_FIELDS,
        entity_flags,
    )

    _write_csv(
        DANGLING_REPORT,
        DANGLING_FIELDS,
        dangling_references,
    )

    print()
    print(
        f"Entity-check report: {ENTITY_FLAG_REPORT}"
    )
    print(
        f"Dangling-reference report: {DANGLING_REPORT}"
    )

    print()

    if dangling_references:
        print(
            "ERROR: Dangling source references were detected. "
            "Review reports/dangling_source_references.csv."
        )
    else:
        print(
            "PASS: No dangling source references were detected "
            "in the deterministic sample."
        )

    if entity_flags:
        print(
            "INFO: Accepted fuzzy matches with materially changed "
            "scores were flagged. No records were changed."
        )
    else:
        print(
            "PASS: No accepted fuzzy matches in the sample had a "
            f"score drop >= {SCORE_DROP_THRESHOLD}."
        )

    print()
    print("=" * 72)
    print("Entity-resolution validation completed.")
    print("=" * 72)


def main() -> None:
    run_entity_checks()


if __name__ == "__main__":
    main()
