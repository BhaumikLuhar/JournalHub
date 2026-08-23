from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

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


load_dotenv("database/.env")

logger = logging.getLogger(__name__)

SOURCE_CODE = "SCIMAGO"
SOURCE_RECORD_TABLE = "scimago_records"
SOURCE_IDENTIFIER_TYPE = "SCIMAGO_SOURCE_ID"


def get_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "journal_platform"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD"),
    )


def get_source_id(cur) -> int:
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
        raise RuntimeError(
            f"Source {SOURCE_CODE!r} not found"
        )

    return row[0]


def get_affected_sourceids(cur) -> list[str]:
    """
    Find SCImago Sourceids that do not yet have their own
    SCIMAGO_SOURCE_ID identifier.

    These are the 19 Sourceids collapsed into existing canonical
    journals during the first Day 5 run.
    """
    cur.execute(
        """
        SELECT DISTINCT sr.sourceid
        FROM scimago_records sr
        WHERE NOT EXISTS (
            SELECT 1
            FROM journal_identifiers ji
            WHERE ji.identifier_type = %s
              AND ji.normalized_value = BTRIM(sr.sourceid)
        )
        ORDER BY sr.sourceid
        """,
        (SOURCE_IDENTIFIER_TYPE,),
    )

    return [row[0] for row in cur.fetchall()]


def get_sourceid_records(
    cur,
    sourceid: str,
) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT
            id,
            sourceid,
            title,
            issn_raw,
            publisher_raw,
            subject_area,
            year,
            journal_id
        FROM scimago_records
        WHERE sourceid = %s
        ORDER BY
            year DESC,
            subject_area ASC,
            id ASC
        """,
        (sourceid,),
    )

    rows = cur.fetchall()

    if not rows:
        raise RuntimeError(
            f"No SCImago records found for sourceid {sourceid!r}"
        )

    return [
        {
            "id": row[0],
            "sourceid": row[1],
            "title": row[2],
            "issn_raw": row[3],
            "publisher_raw": row[4],
            "subject_area": row[5],
            "year": row[6],
            "journal_id": row[7],
        }
        for row in rows
    ]


def choose_representative_row(
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Apply the exact Day 5 deterministic representative rule.
    """
    years = [
        record["year"]
        for record in records
        if record["year"] is not None
    ]

    if not years:
        raise RuntimeError(
            f"No year found for sourceid {records[0]['sourceid']!r}"
        )

    max_year = max(years)

    latest_rows = [
        record
        for record in records
        if record["year"] == max_year
    ]

    rows_with_publisher = [
        record
        for record in latest_rows
        if record["publisher_raw"] is not None
        and str(record["publisher_raw"]).strip()
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


def collect_issns(
    records: list[dict[str, Any]],
) -> list[str]:
    """
    Collect all distinct raw ISSN values across every year and
    subject-area row for this Sourceid.
    """
    seen: set[str] = set()
    result: list[str] = []

    for record in records:
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


def collect_title_variants(
    records: list[dict[str, Any]],
    representative_title: str,
) -> list[str]:
    variants: set[str] = set()

    for record in records:
        title = record["title"]

        if title is None:
            continue

        normalized_display = normalize_title(title)

        if not normalized_display:
            continue

        if normalized_display == representative_title:
            continue

        variants.add(normalized_display)

    return sorted(variants)


def insert_aliases(
    cur,
    *,
    journal_id: int,
    source_id: int,
    aliases: list[str],
) -> None:
    for alias_name in aliases:
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


def update_source_mappings(
    cur,
    *,
    journal_id: int,
    source_id: int,
    records: list[dict[str, Any]],
) -> None:
    for record in records:
        cur.execute(
            """
            UPDATE journal_source_mapping
            SET journal_id = %s,
                match_method = %s,
                match_score = %s,
                match_status = %s
            WHERE source_id = %s
              AND source_record_table = %s
              AND source_record_id = %s
            """,
            (
                journal_id,
                "source_id",
                1.0,
                "accepted",
                source_id,
                SOURCE_RECORD_TABLE,
                record["id"],
            ),
        )

        if cur.rowcount != 1:
            raise RuntimeError(
                "Expected exactly one source mapping for "
                f"SCImago record {record['id']}, but updated "
                f"{cur.rowcount}"
            )


def update_match_decisions(
    cur,
    *,
    journal_id: int,
    source_id: int,
    records: list[dict[str, Any]],
) -> None:
    for record in records:
        cur.execute(
            """
            UPDATE entity_match_decisions
            SET journal_id = %s,
                match_method = %s,
                confidence = %s,
                decision = %s
            WHERE source_id = %s
              AND source_record_table = %s
              AND source_record_id = %s
            """,
            (
                journal_id,
                "source_id",
                1.0,
                "accepted",
                source_id,
                SOURCE_RECORD_TABLE,
                record["id"],
            ),
        )

        if cur.rowcount != 1:
            raise RuntimeError(
                "Expected exactly one entity-match decision for "
                f"SCImago record {record['id']}, but updated "
                f"{cur.rowcount}"
            )


def repair_sourceid(
    cur,
    *,
    source_id: int,
    sourceid: str,
) -> int:
    records = get_sourceid_records(cur, sourceid)

    existing_journal_ids = {
        record["journal_id"]
        for record in records
        if record["journal_id"] is not None
    }

    if len(existing_journal_ids) != 1:
        raise RuntimeError(
            f"Expected exactly one current canonical journal for "
            f"sourceid {sourceid!r}, found "
            f"{sorted(existing_journal_ids)}"
        )

    old_journal_id = next(iter(existing_journal_ids))

    representative = choose_representative_row(records)

    representative_title = normalize_title(
        representative["title"]
    )

    if not representative_title:
        raise RuntimeError(
            f"Empty representative title for sourceid {sourceid!r}"
        )

    matching_title = normalized_matching_title(
        representative_title
    )

    if not matching_title:
        raise RuntimeError(
            f"Empty normalized title for sourceid {sourceid!r}"
        )

    issn_list = collect_issns(records)

    years = [
        record["year"]
        for record in records
        if record["year"] is not None
    ]

    if not years:
        raise RuntimeError(
            f"No years for sourceid {sourceid!r}"
        )

    first_observed_year = min(years)

    representative_publisher = (
        representative["publisher_raw"]
    )

    # Because canonical.py was corrected so that a strong source
    # identifier does not fall through to title matching, this must
    # create a new journal when this Sourceid does not already exist.
    new_journal_id, was_created = (
        get_or_create_canonical_journal(
            candidate_title=representative_title,
            matching_title=matching_title,
            issn_list=issn_list,
            source_id=source_id,
            source_identifier_type=SOURCE_IDENTIFIER_TYPE,
            source_identifier_value=sourceid,
            publisher=representative_publisher,
            observed_year=first_observed_year,
            conn=cur.connection,
        )
    )

    if not was_created:
        raise RuntimeError(
            f"Repair expected a new journal for sourceid "
            f"{sourceid!r}, but helper reused journal "
            f"{new_journal_id}"
        )

    if new_journal_id == old_journal_id:
        raise RuntimeError(
            f"Repair unexpectedly returned old journal "
            f"{old_journal_id} for sourceid {sourceid!r}"
        )

    aliases = collect_title_variants(
        records,
        representative_title,
    )

    insert_aliases(
        cur,
        journal_id=new_journal_id,
        source_id=source_id,
        aliases=aliases,
    )

    # Move all SCImago records for this Sourceid.
    cur.execute(
        """
        UPDATE scimago_records
        SET journal_id = %s
        WHERE sourceid = %s
        """,
        (
            new_journal_id,
            sourceid,
        ),
    )

    if cur.rowcount != len(records):
        raise RuntimeError(
            f"Expected to move {len(records)} SCImago records for "
            f"sourceid {sourceid!r}, but moved {cur.rowcount}"
        )

    update_source_mappings(
        cur,
        journal_id=new_journal_id,
        source_id=source_id,
        records=records,
    )

    update_match_decisions(
        cur,
        journal_id=new_journal_id,
        source_id=source_id,
        records=records,
    )

    logger.info(
        "Repaired sourceid=%s: old_journal=%s → new_journal=%s "
        "(records=%d)",
        sourceid,
        old_journal_id,
        new_journal_id,
        len(records),
    )

    return new_journal_id


def verify_repair(
    cur,
    *,
    source_id: int,
    repaired_sourceids: list[str],
) -> None:
    # ---------------------------------------------------------
    # 1. Every repaired Sourceid must now have exactly one
    #    SCIMAGO_SOURCE_ID identifier.
    # ---------------------------------------------------------
    for sourceid in repaired_sourceids:
        cur.execute(
            """
            SELECT COUNT(*)
            FROM journal_identifiers
            WHERE identifier_type = %s
              AND normalized_value = %s
            """,
            (
                SOURCE_IDENTIFIER_TYPE,
                sourceid,
            ),
        )

        count = cur.fetchone()[0]

        if count != 1:
            raise RuntimeError(
                f"Sourceid {sourceid!r} has {count} "
                f"SCIMAGO_SOURCE_ID identifiers after repair"
            )

    # ---------------------------------------------------------
    # 2. Every repaired Sourceid must map to exactly one journal.
    # ---------------------------------------------------------
    cur.execute(
        """
        SELECT
            sourceid,
            COUNT(DISTINCT journal_id)
        FROM scimago_records
        WHERE sourceid = ANY(%s)
        GROUP BY sourceid
        ORDER BY sourceid
        """,
        (repaired_sourceids,),
    )

    rows = cur.fetchall()

    if len(rows) != len(repaired_sourceids):
        raise RuntimeError(
            "Not all repaired Sourceids were found during "
            "post-repair verification"
        )

    for sourceid, journal_count in rows:
        if journal_count != 1:
            raise RuntimeError(
                f"Sourceid {sourceid!r} maps to "
                f"{journal_count} journals"
            )

    # ---------------------------------------------------------
    # 3. Every repaired Sourceid's identifier must point to the
    #    same journal as its SCImago records.
    # ---------------------------------------------------------
    cur.execute(
        """
        SELECT
            sr.sourceid,
            COUNT(DISTINCT sr.journal_id),
            COUNT(DISTINCT ji.journal_id)
        FROM scimago_records sr
        JOIN journal_identifiers ji
          ON ji.identifier_type = %s
         AND ji.normalized_value = BTRIM(sr.sourceid)
        WHERE sr.sourceid = ANY(%s)
        GROUP BY sr.sourceid
        ORDER BY sr.sourceid
        """,
        (
            SOURCE_IDENTIFIER_TYPE,
            repaired_sourceids,
        ),
    )

    rows = cur.fetchall()

    for sourceid, record_journal_count, identifier_journal_count in rows:
        if record_journal_count != 1:
            raise RuntimeError(
                f"Sourceid {sourceid!r} has "
                f"{record_journal_count} record-linked journals"
            )

        if identifier_journal_count != 1:
            raise RuntimeError(
                f"Sourceid {sourceid!r} has "
                f"{identifier_journal_count} identifier-linked journals"
            )

    # ---------------------------------------------------------
    # 4. Global Day 5 invariants.
    # ---------------------------------------------------------
    cur.execute(
        """
        SELECT COUNT(DISTINCT sourceid)
        FROM scimago_records
        """
    )
    sourceid_count = cur.fetchone()[0]

    cur.execute(
        """
        SELECT COUNT(*)
        FROM journals
        """
    )
    journal_count = cur.fetchone()[0]

    if sourceid_count != journal_count:
        raise RuntimeError(
            f"Day 5 canonical count mismatch: "
            f"{sourceid_count} Sourceids vs "
            f"{journal_count} journals"
        )

    cur.execute(
        """
        SELECT COUNT(*)
        FROM scimago_records
        WHERE journal_id IS NULL
        """
    )
    unlinked = cur.fetchone()[0]

    if unlinked != 0:
        raise RuntimeError(
            f"{unlinked} SCImago records remain unlinked"
        )

    cur.execute(
        """
        SELECT COUNT(*)
        FROM journal_source_mapping
        WHERE source_record_table = %s
        """,
        (SOURCE_RECORD_TABLE,),
    )
    mapping_count = cur.fetchone()[0]

    cur.execute(
        """
        SELECT COUNT(*)
        FROM scimago_records
        """
    )
    scimago_count = cur.fetchone()[0]

    if mapping_count != scimago_count:
        raise RuntimeError(
            f"Mapping count mismatch: "
            f"{mapping_count} mappings vs "
            f"{scimago_count} SCImago records"
        )

    cur.execute(
        """
        SELECT COUNT(*)
        FROM entity_match_decisions
        WHERE source_record_table = %s
        """,
        (SOURCE_RECORD_TABLE,),
    )
    decision_count = cur.fetchone()[0]

    if decision_count != scimago_count:
        raise RuntimeError(
            f"Decision count mismatch: "
            f"{decision_count} decisions vs "
            f"{scimago_count} SCImago records"
        )

    cur.execute(
        """
        SELECT COUNT(*)
        FROM journal_identifiers
        WHERE identifier_type = %s
        """,
        (SOURCE_IDENTIFIER_TYPE,),
    )
    source_identifier_count = cur.fetchone()[0]

    if source_identifier_count != sourceid_count:
        raise RuntimeError(
            f"SCImago Sourceid identifier count mismatch: "
            f"{source_identifier_count} identifiers vs "
            f"{sourceid_count} Sourceids"
        )

    logger.info(
        "Repair verification passed: "
        "sourceids=%d, journals=%d, "
        "unlinked=%d, mappings=%d, decisions=%d, "
        "SCIMAGO_SOURCE_IDs=%d",
        sourceid_count,
        journal_count,
        unlinked,
        mapping_count,
        decision_count,
        source_identifier_count,
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    conn = get_connection()

    try:
        with conn:
            with conn.cursor() as cur:
                source_id = get_source_id(cur)

                affected_sourceids = get_affected_sourceids(cur)

                logger.info(
                    "Found %d SCImago Sourceids requiring repair",
                    len(affected_sourceids),
                )

                if len(affected_sourceids) != 19:
                    raise RuntimeError(
                        "Expected exactly 19 affected Sourceids, "
                        f"found {len(affected_sourceids)}"
                    )

                repaired_journals: list[int] = []

                for sourceid in affected_sourceids:
                    journal_id = repair_sourceid(
                        cur,
                        source_id=source_id,
                        sourceid=sourceid,
                    )

                    repaired_journals.append(journal_id)

                if len(set(repaired_journals)) != 19:
                    raise RuntimeError(
                        "Repair did not create 19 distinct canonical "
                        "journals"
                    )

                verify_repair(
                    cur,
                    source_id=source_id,
                    repaired_sourceids=affected_sourceids,
                )

        logger.info(
            "SCImago Sourceid collision repair committed successfully."
        )

    except Exception:
        logger.exception(
            "SCImago Sourceid repair failed; "
            "transaction rolled back."
        )
        raise

    finally:
        conn.close()


if __name__ == "__main__":
    main()