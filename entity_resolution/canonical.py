import csv
import os
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

from ingestion.common.normalization import normalize_issn


load_dotenv("database/.env")


def _get_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "journal_platform"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD"),
    )


def _log_identifier_conflict(message):
    path = Path("reports/issn_conflicts.csv")
    path.parent.mkdir(parents=True, exist_ok=True)

    exists = path.exists()

    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)

        if not exists:
            writer.writerow(["message"])

        writer.writerow([message])


def _normalize_identifier(identifier_type, identifier_value):
    if identifier_value is None:
        return None

    value = str(identifier_value).strip()

    if not value:
        return None

    if identifier_type in ("ISSN", "EISSN"):
        return normalize_issn(value)

    return value


def _find_identifier(cur, identifier_type, normalized_value):
    cur.execute(
        """
        SELECT journal_id
        FROM journal_identifiers
        WHERE identifier_type = %s
          AND normalized_value = %s
        """,
        (identifier_type, normalized_value),
    )

    row = cur.fetchone()

    if row is None:
        return None

    return row[0]


def get_or_create_canonical_journal(
    candidate_title,
    matching_title,
    issn_list,
    source_id,
    source_identifier_type=None,
    source_identifier_value=None,
    publisher=None,
    observed_year=None,
):
    """
    Find an existing canonical journal or create one.

    Returns:
        (journal_id, was_created)

    Sequentially idempotent; this pipeline assumes a single sequential import process.
    """

    conn = _get_connection()

    try:
        with conn:
            with conn.cursor() as cur:

                # ---------------------------------------------------------
                # 1. Exact source identifier match
                # ---------------------------------------------------------
                if source_identifier_type and source_identifier_value:
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
                # If there is a source identifier, it is the stronger
                # identity signal. An ISSN belonging to another journal
                # must not prevent creation of the source-identified
                # journal. The conflicting identifier will be handled
                # independently after journal creation.
                # ---------------------------------------------------------
                if not (
                    source_identifier_type
                    and source_identifier_value
                ):
                    matched_journal_ids = set()

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
                # ---------------------------------------------------------
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
                # 4a. Register source identifier independently
                # ---------------------------------------------------------
                identifiers = []

                if source_identifier_type and source_identifier_value:
                    identifiers.append(
                        (
                            source_identifier_type,
                            source_identifier_value,
                        )
                    )

                # ---------------------------------------------------------
                # 4b. Register every ISSN independently
                # ---------------------------------------------------------
                for issn in issn_list or []:
                    if issn is not None:
                        identifiers.append(("ISSN", issn))

                # ---------------------------------------------------------
                # 4c. Attach identifiers independently
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
                # 4d. Register candidate title as alias when different
                # ---------------------------------------------------------
                if candidate_title != matching_title:
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
                            candidate_title,
                            matching_title,
                        ),
                    )

                return journal_id, True

    finally:
        conn.close()
