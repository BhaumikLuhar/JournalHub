from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

# Make direct execution work:
#
#     python pipelines/build_canonical_from_scimago.py
#
# when executed from the project root.
PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import psycopg2
from dotenv import load_dotenv

from entity_resolution.canonical import (
    get_or_create_canonical_journal,
)
from ingestion.common.normalization import (
    normalize_title,
    normalized_matching_title,
)


logger = logging.getLogger(__name__)

load_dotenv("database/.env")


SOURCE_CODE = "SCIMAGO"
SOURCE_RECORD_TABLE = "scimago_records"


class RollbackTestCompleted(Exception):
    """Internal control-flow exception used to force smoke-test rollback."""


def _get_connection():
    """Open a PostgreSQL connection using project database settings."""
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "journal_platform"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD"),
    )


def _get_source_id(cur) -> int:
    """Return the database source ID for SCImago."""
    cur.execute(
        """
        SELECT id
        FROM sources
        WHERE code = %s
        """,
        (SOURCE_CODE,),
    )

    row = cur.fetchone()

    if row is None:
        raise ValueError(
            f"Source {SOURCE_CODE!r} does not exist in sources"
        )

    return row[0]


def _iter_scimago_records(cur) -> Iterator[dict[str, Any]]:
    """
    Yield SCImago records from a dedicated read cursor.

    The cursor passed here must never be reused for canonicalization writes.
    """
    cur.execute(
        """
        SELECT
            id,
            sourceid,
            title,
            issn_raw,
            publisher_raw,
            subject_area,
            year
        FROM scimago_records
        ORDER BY
            sourceid,
            year DESC,
            subject_area ASC,
            id ASC
        """
    )

    for row in cur:
        yield {
            "id": row[0],
            "sourceid": row[1],
            "title": row[2],
            "issn_raw": row[3],
            "publisher_raw": row[4],
            "subject_area": row[5],
            "year": row[6],
        }


def _split_sourceid_groups(
    records: Iterator[dict[str, Any]],
) -> Iterator[list[dict[str, Any]]]:
    """
    Yield one complete SCImago record group per sourceid.

    The input must already be ordered by sourceid.
    """
    current_sourceid: str | None = None
    current_group: list[dict[str, Any]] = []

    for record in records:
        sourceid = record["sourceid"]

        if current_sourceid is None:
            current_sourceid = sourceid

        if sourceid != current_sourceid:
            yield current_group

            current_group = []
            current_sourceid = sourceid

        current_group.append(record)

    if current_group:
        yield current_group


def _choose_representative_row(
    group: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Choose the deterministic representative SCImago row.

    Rule:
        1. Find the highest year in the group.
        2. Among latest-year rows, prefer rows with non-null publisher.
        3. Within that preferred set, choose alphabetically-first
           subject_area.
        4. If no latest-year row has a publisher, choose alphabetically-first
           subject_area regardless of publisher.
        5. id is a final deterministic tie-breaker.
    """
    max_year = max(record["year"] for record in group)

    latest_rows = [
        record
        for record in group
        if record["year"] == max_year
    ]

    rows_with_publisher = [
        record
        for record in latest_rows
        if record["publisher_raw"] is not None
        and str(record["publisher_raw"]).strip() != ""
    ]

    candidates = (
        rows_with_publisher
        if rows_with_publisher
        else latest_rows
    )

    return min(
        candidates,
        key=lambda record: (
            str(record["subject_area"]).casefold(),
            str(record["subject_area"]),
            record["id"],
        ),
    )


def _collect_issns(
    group: list[dict[str, Any]],
) -> list[str]:
    """
    Collect the union of all ISSNs observed for a Sourceid.

    SCImago's issn_raw may contain multiple comma-separated identifiers.
    Stable textual de-duplication is performed here; canonical identifier
    normalization remains the responsibility of the canonical helper.
    """
    seen: set[str] = set()
    result: list[str] = []

    for record in group:
        raw = record["issn_raw"]

        if raw is None:
            continue

        for value in str(raw).split(","):
            value = value.strip()

            if not value:
                continue

            if value not in seen:
                seen.add(value)
                result.append(value)

    return result


def _collect_title_variants(
    group: list[dict[str, Any]],
    representative_title: str,
) -> list[str]:
    """
    Collect genuine title variants different from the representative title.

    The representative title is never inserted as its own alias.
    """
    variants: set[str] = set()

    for record in group:
        title = record["title"]

        if title is None:
            continue

        title = normalize_title(title)

        if not title:
            continue

        if title == representative_title:
            continue

        variants.add(title)

    return sorted(variants)


def _insert_title_aliases(
    cur,
    *,
    journal_id: int,
    source_id: int,
    title_variants: list[str],
) -> None:
    """Insert genuine SCImago title variants as aliases."""
    for alias_name in title_variants:
        normalized_alias = normalized_matching_title(alias_name)

        if not normalized_alias:
            continue

        cur.execute(
            """
            INSERT INTO journal_aliases (
                journal_id,
                source_id,
                alias_name,
                normalized_alias
            )
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (
                journal_id,
                normalized_alias
            )
            DO NOTHING
            """,
            (
                journal_id,
                source_id,
                alias_name,
                normalized_alias,
            ),
        )


def _insert_source_mapping(
    cur,
    *,
    journal_id: int,
    source_id: int,
    source_record_id: int,
) -> None:
    """Create the accepted SCImago source-to-journal mapping."""
    cur.execute(
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
            SOURCE_RECORD_TABLE,
            source_record_id,
            "source_id",
            1.0,
            "accepted",
        ),
    )


def _insert_match_decision(
    cur,
    *,
    journal_id: int,
    source_id: int,
    source_record_id: int,
) -> None:
    """Create the accepted SCImago entity-resolution decision."""
    cur.execute(
        """
        INSERT INTO entity_match_decisions (
            source_id,
            source_record_table,
            source_record_id,
            journal_id,
            match_method,
            confidence,
            decision
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
            source_id,
            SOURCE_RECORD_TABLE,
            source_record_id,
            journal_id,
            "source_id",
            1.0,
            "accepted",
        ),
    )


def _link_scimago_group(
    cur,
    *,
    source_id: int,
    group: list[dict[str, Any]],
) -> tuple[int, bool]:
    """
    Canonicalize one complete SCImago Sourceid group.

    Returns:
        (journal_id, was_created)
    """
    sourceid = group[0]["sourceid"]

    representative = _choose_representative_row(group)

    representative_title = normalize_title(
        representative["title"]
    )

    if not representative_title:
        raise ValueError(
            f"Empty representative title for SCImago sourceid "
            f"{sourceid!r}"
        )

    matching_title = normalized_matching_title(
        representative_title
    )

    if not matching_title:
        raise ValueError(
            f"Empty normalized matching title for SCImago sourceid "
            f"{sourceid!r}"
        )

    issn_list = _collect_issns(group)

    years_seen = [
        record["year"]
        for record in group
        if record["year"] is not None
    ]

    if not years_seen:
        raise ValueError(
            f"No year values found for SCImago sourceid {sourceid!r}"
        )

    first_observed_year = min(years_seen)

    representative_publisher = (
        representative["publisher_raw"]
    )

    journal_id, was_created = get_or_create_canonical_journal(
        candidate_title=representative_title,
        matching_title=matching_title,
        issn_list=issn_list,
        source_id=source_id,
        source_identifier_type="SCIMAGO_SOURCE_ID",
        source_identifier_value=sourceid,
        publisher=representative_publisher,
        observed_year=first_observed_year,
        conn=cur.connection,
    )

    title_variants = _collect_title_variants(
        group,
        representative_title,
    )

    _insert_title_aliases(
        cur,
        journal_id=journal_id,
        source_id=source_id,
        title_variants=title_variants,
    )

    for record in group:
        _insert_source_mapping(
            cur,
            journal_id=journal_id,
            source_id=source_id,
            source_record_id=record["id"],
        )

        _insert_match_decision(
            cur,
            journal_id=journal_id,
            source_id=source_id,
            source_record_id=record["id"],
        )

    cur.execute(
        """
        UPDATE scimago_records
        SET journal_id = %s
        WHERE sourceid = %s
        """,
        (
            journal_id,
            sourceid,
        ),
    )

    updated_count = cur.rowcount

    if updated_count != len(group):
        raise RuntimeError(
            f"SCImago sourceid {sourceid!r}: expected to update "
            f"{len(group)} records but updated {updated_count}"
        )

    return journal_id, was_created


def _print_smoke_test_summary(
    cur,
    *,
    processed_sourceids: int,
    processed_records: int,
    created_journals: int,
) -> None:
    """Print database state while still inside the smoke-test transaction."""
    cur.execute(
        """
        SELECT COUNT(*)
        FROM journals
        WHERE canonical_title IS NOT NULL
        """
    )
    journal_count = cur.fetchone()[0]

    cur.execute(
        """
        SELECT COUNT(*)
        FROM journal_identifiers
        """
    )
    identifier_count = cur.fetchone()[0]

    cur.execute(
        """
        SELECT COUNT(*)
        FROM journal_aliases
        """
    )
    alias_count = cur.fetchone()[0]

    cur.execute(
        """
        SELECT COUNT(*)
        FROM journal_source_mapping
        """
    )
    mapping_count = cur.fetchone()[0]

    cur.execute(
        """
        SELECT COUNT(*)
        FROM entity_match_decisions
        """
    )
    decision_count = cur.fetchone()[0]

    cur.execute(
        """
        SELECT COUNT(*)
        FROM scimago_records
        WHERE journal_id IS NOT NULL
        """
    )
    linked_count = cur.fetchone()[0]

    logger.info(
        "SMOKE TEST SUMMARY: "
        "processed_sourceids=%d, "
        "processed_records=%d, "
        "created_journals=%d, "
        "journals=%d, "
        "identifiers=%d, "
        "aliases=%d, "
        "mappings=%d, "
        "decisions=%d, "
        "linked_records=%d",
        processed_sourceids,
        processed_records,
        created_journals,
        journal_count,
        identifier_count,
        alias_count,
        mapping_count,
        decision_count,
        linked_count,
    )


def build_canonical_from_scimago(
    *,
    limit_sourceids: int | None = None,
    rollback_test: bool = False,
) -> None:
    """
    Build the canonical journal layer from SCImago.

    Normal mode:
        Processes every Sourceid and commits the transaction.

    Smoke-test mode:
        Processes only limit_sourceids Sourceids and requires
        rollback_test=True. The transaction is deliberately rolled back
        after validation so the real database remains unchanged.
    """
    if limit_sourceids is not None and limit_sourceids <= 0:
        raise ValueError(
            "--limit-sourceids must be greater than zero"
        )

    if rollback_test and limit_sourceids is None:
        raise ValueError(
            "--rollback-test requires --limit-sourceids"
        )

    conn = _get_connection()

    processed_sourceids = 0
    created_journals = 0
    reused_journals = 0
    processed_records = 0

    try:
        with conn:
            with conn.cursor() as write_cur:
                source_id = _get_source_id(write_cur)

                # A named/server-side cursor prevents all 139,491 SCImago
                # rows from being loaded into Python memory at once.
                #
                # It is deliberately separate from write_cur because the
                # canonicalization process performs SELECT/INSERT/UPDATE
                # operations while the SCImago result set is being consumed.
                with conn.cursor(
                    name="scimago_read_cursor"
                ) as read_cur:
                    records = _iter_scimago_records(read_cur)

                    for group in _split_sourceid_groups(records):
                        if (
                            limit_sourceids is not None
                            and processed_sourceids >= limit_sourceids
                        ):
                            break

                        journal_id, was_created = _link_scimago_group(
                            write_cur,
                            source_id=source_id,
                            group=group,
                        )

                        processed_sourceids += 1
                        processed_records += len(group)

                        if was_created:
                            created_journals += 1
                        else:
                            reused_journals += 1

                        if processed_sourceids % 500 == 0:
                            logger.info(
                                "Processed %d Sourceids; "
                                "%d SCImago records; "
                                "%d journals created; "
                                "%d journals reused",
                                processed_sourceids,
                                processed_records,
                                created_journals,
                                reused_journals,
                            )

                        logger.debug(
                            "Resolved SCImago sourceid=%s "
                            "to journal_id=%s "
                            "(created=%s)",
                            group[0]["sourceid"],
                            journal_id,
                            was_created,
                        )

                if limit_sourceids is not None:
                    if processed_sourceids != limit_sourceids:
                        raise RuntimeError(
                            "Smoke test expected to process "
                            f"{limit_sourceids} Sourceids but processed "
                            f"{processed_sourceids}"
                        )

                if rollback_test:
                    logger.info(
                        "Smoke test processed %d Sourceids and %d "
                        "SCImago records successfully.",
                        processed_sourceids,
                        processed_records,
                    )

                    _print_smoke_test_summary(
                        write_cur,
                        processed_sourceids=processed_sourceids,
                        processed_records=processed_records,
                        created_journals=created_journals,
                    )

                    logger.info(
                        "Deliberately rolling back smoke-test transaction."
                    )

                    raise RollbackTestCompleted()

        logger.info(
            "SCImago canonical build completed successfully: "
            "%d Sourceids, %d records, %d journals created, "
            "%d journals reused",
            processed_sourceids,
            processed_records,
            created_journals,
            reused_journals,
        )

    except RollbackTestCompleted:
        # The exception occurs inside the transaction context, so psycopg2
        # has already rolled the smoke-test transaction back before this
        # handler executes.
        logger.info(
            "Smoke-test rollback completed successfully. "
            "No smoke-test canonical data was committed."
        )

    except Exception:
        logger.exception(
            "SCImago canonical build failed; transaction rolled back"
        )
        raise

    finally:
        conn.close()


def _parse_args() -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(
        description=(
            "Build canonical journals from SCImago Sourceids."
        )
    )

    parser.add_argument(
        "--limit-sourceids",
        type=int,
        default=None,
        help=(
            "Process only this many Sourceids. "
            "Must be combined with --rollback-test."
        ),
    )

    parser.add_argument(
        "--rollback-test",
        action="store_true",
        help=(
            "Run a limited smoke test and deliberately roll back "
            "all database changes."
        ),
    )

    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    args = _parse_args()

    build_canonical_from_scimago(
        limit_sourceids=args.limit_sourceids,
        rollback_test=args.rollback_test,
    )


if __name__ == "__main__":
    main()