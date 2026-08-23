from __future__ import annotations
import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import csv
import os
from pathlib import Path
from typing import Any

import psycopg2
from dotenv import load_dotenv

from ingestion.common.normalization import normalize_issn


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


def _log_identifier_conflict(message: str) -> None:
    """Append an identifier/title conflict to the project conflict report."""
    path = Path("reports/issn_conflicts.csv")
    path.parent.mkdir(parents=True, exist_ok=True)

    exists = path.exists()

    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)

        if not exists:
            writer.writerow(["message"])

        writer.writerow([message])


def _normalize_identifier(
    identifier_type: str,
    identifier_value: Any,
) -> str | None:
    """Normalize an identifier according to its controlled type."""
    if identifier_value is None:
        return None

    value = str(identifier_value).strip()

    if not value:
        return None

    if identifier_type in ("ISSN", "EISSN"):
        return normalize_issn(value)

    return value


def _find_identifier(
    cur,
    identifier_type: str,
    normalized_value: str,
) -> int | None:
    """Return the journal owning an exact identifier, or None."""
    cur.execute(
        """
        SELECT journal_id
        FROM journal_identifiers
        WHERE identifier_type = %s
          AND normalized_value = %s
        """,
        (
            identifier_type,
            normalized_value,
        ),
    )

    row = cur.fetchone()

    if row is None:
        return None

    return row[0]


def _get_or_create_with_cursor(
    cur,
    *,
    candidate_title: str,
    matching_title: str,
    issn_list: list[str] | None,
    source_id: int,
    source_identifier_type: str | None,
    source_identifier_value: str | None,
    publisher: str | None,
    observed_year: int | None,
) -> tuple[int, bool]:
    """
    Perform canonical-journal resolution using an existing cursor.

    The caller owns the transaction represented by this cursor.

    Returns:
        (journal_id, was_created)
    """

        # ---------------------------------------------------------
    # 1. Strong source identifier resolution
    #
    # When the caller supplies a source-specific identifier,
    # that identifier is authoritative for this resolution.
    #
    # If it already exists, reuse the exact journal.
    #
    # If it does not exist, DO NOT fall through to ISSN/title
    # matching. Create a new canonical journal for this source
    # identifier.
    # ---------------------------------------------------------
    has_source_identifier = bool(
        source_identifier_type
        and source_identifier_value
    )

    if has_source_identifier:
        normalized_source_identifier = _normalize_identifier(
            source_identifier_type,
            source_identifier_value,
        )

        if normalized_source_identifier:
            journal_id = _find_identifier(
                cur,
                source_identifier_type,
                normalized_source_identifier,
            )

            if journal_id is not None:
                return journal_id, False

    # ---------------------------------------------------------
    # 2. ISSN / EISSN matching
    #
    # Only perform generic identifier matching when there is no
    # authoritative source-specific identifier.
    # ---------------------------------------------------------
    if not has_source_identifier:
        matched_journal_ids: set[int] = set()

        for issn in issn_list or []:
            normalized_issn = _normalize_identifier(
                "ISSN",
                issn,
            )

            if normalized_issn is None:
                continue

            cur.execute(
                """
                SELECT DISTINCT journal_id
                FROM journal_identifiers
                WHERE identifier_type IN ('ISSN', 'EISSN')
                  AND normalized_value = %s
                """,
                (normalized_issn,),
            )

            for row in cur.fetchall():
                matched_journal_ids.add(row[0])

        if len(matched_journal_ids) == 1:
            return matched_journal_ids.pop(), False

        if len(matched_journal_ids) > 1:
            _log_identifier_conflict(
                "ISSN conflict: source record matched multiple "
                f"journals {sorted(matched_journal_ids)}; "
                "falling through to title matching"
            )

    # ---------------------------------------------------------
    # 3. Normalized title matching
    #
    # Title matching is also generic resolution behavior and is
    # therefore disabled when a strong source identifier was
    # supplied but was not found.
    # ---------------------------------------------------------
    if not has_source_identifier:
        cur.execute(
            """
            SELECT id
            FROM journals
            WHERE normalized_title = %s
            ORDER BY id
            """,
            (matching_title,),
        )

        title_matches = [row[0] for row in cur.fetchall()]

        if len(title_matches) == 1:
            return title_matches[0], False

        if len(title_matches) > 1:
            _log_identifier_conflict(
                "Ambiguous normalized title match: "
                f"{matching_title!r} matched journals "
                f"{title_matches}"
            )

            raise ValueError(
                "Ambiguous canonical journal match for "
                f"normalized title {matching_title!r}: "
                f"journal IDs {title_matches}"
            )

    # ---------------------------------------------------------
    # 4. Create new canonical journal
    # ---------------------------------------------------------
    cur.execute(
        """
        INSERT INTO journals (
            canonical_title,
            normalized_title,
            publisher,
            first_observed_year
        )
        VALUES (%s, %s, %s, %s)
        RETURNING id
        """,
        (
            candidate_title,
            matching_title,
            publisher,
            observed_year,
        ),
    )

    journal_id = cur.fetchone()[0]

    # ---------------------------------------------------------
    # 4a. Prepare source identifier
    # ---------------------------------------------------------
    identifiers: list[tuple[str, Any]] = []

    if source_identifier_type and source_identifier_value:
        identifiers.append(
            (
                source_identifier_type,
                source_identifier_value,
            )
        )

    # ---------------------------------------------------------
    # 4b. Prepare all ISSNs
    # ---------------------------------------------------------
    for issn in issn_list or []:
        if issn is not None:
            identifiers.append(
                (
                    "ISSN",
                    issn,
                )
            )

    # ---------------------------------------------------------
    # 4c. Attach identifiers independently
    #
    # A conflict on one identifier does not abort creation of
    # the new canonical journal or prevent other identifiers
    # from being registered.
    # ---------------------------------------------------------
    for identifier_type, identifier_value in identifiers:
        normalized_value = _normalize_identifier(
            identifier_type,
            identifier_value,
        )

        if normalized_value is None:
            continue

        existing_journal_id = _find_identifier(
            cur,
            identifier_type,
            normalized_value,
        )

        if existing_journal_id is None:
            cur.execute(
                """
                INSERT INTO journal_identifiers (
                    journal_id,
                    identifier_type,
                    identifier_value,
                    normalized_value,
                    source_id,
                    is_primary
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    journal_id,
                    identifier_type,
                    str(identifier_value).strip(),
                    normalized_value,
                    source_id,
                    False,
                ),
            )

        elif existing_journal_id == journal_id:
            # Already attached to this journal.
            pass

        else:
            _log_identifier_conflict(
                "identifier "
                f"{identifier_type}:{identifier_value} "
                f"already belongs to journal "
                f"{existing_journal_id}, could not attach "
                f"to newly created journal {journal_id}"
            )

    # ---------------------------------------------------------
    # 4d. Alias handling
    #
    # candidate_title is the canonical display title when a
    # new journal is created. Therefore it must NOT be inserted
    # as its own alias merely because matching_title is a
    # lowercase/matching-only representation.
    #
    # Genuine alternative title variants are registered by the
    # source-specific resolution logic when an existing journal
    # is encountered with a different display title.
    # ---------------------------------------------------------

    return journal_id, True


def get_or_create_canonical_journal(
    candidate_title,
    matching_title,
    issn_list,
    source_id,
    source_identifier_type=None,
    source_identifier_value=None,
    publisher=None,
    observed_year=None,
    conn=None,
):
    """
    Find an existing canonical journal or create one.

    Returns:
        (journal_id, was_created)

    Transaction behavior:
        - conn=None:
            The helper creates and owns its own database connection and
            transaction. This preserves standalone Day 3-style usage.

        - conn=<existing connection>:
            The caller owns the connection and transaction. The helper
            performs all work through that connection and does not commit,
            rollback, or close it.

    This function is sequentially idempotent. It is not a concurrency
    guarantee and assumes the project's single sequential pipeline model.
    """
    owns_connection = conn is None

    if owns_connection:
        conn = _get_connection()

    try:
        if owns_connection:
            with conn:
                with conn.cursor() as cur:
                    return _get_or_create_with_cursor(
                        cur,
                        candidate_title=candidate_title,
                        matching_title=matching_title,
                        issn_list=issn_list,
                        source_id=source_id,
                        source_identifier_type=source_identifier_type,
                        source_identifier_value=source_identifier_value,
                        publisher=publisher,
                        observed_year=observed_year,
                    )

        with conn.cursor() as cur:
            return _get_or_create_with_cursor(
                cur,
                candidate_title=candidate_title,
                matching_title=matching_title,
                issn_list=issn_list,
                source_id=source_id,
                source_identifier_type=source_identifier_type,
                source_identifier_value=source_identifier_value,
                publisher=publisher,
                observed_year=observed_year,
            )

    finally:
        if owns_connection:
            conn.close()