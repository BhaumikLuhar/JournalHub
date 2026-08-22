from __future__ import annotations

from collections.abc import Callable
from typing import Any

import os

import psycopg2
from dotenv import load_dotenv


# These are the normalized tables that reset_dataset() is allowed to target.
# Keeping this allowlist explicit prevents arbitrary SQL identifiers from being
# supplied by a caller.
_ALLOWED_NORMALIZED_TABLES = {
    "scimago_records",
    "abdc_records",
    "abs_records",
    "repec_records",
    "ft50_records",
}


def _get_connection():
    """Open a PostgreSQL connection using the project's database/.env settings."""
    load_dotenv("database/.env")

    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "journal_platform"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD"),
    )


def get_or_create_dataset(
    source_code: str,
    dataset_year: int | None,
    subject_area: str | None,
    file_name: str,
    file_hash: str,
) -> dict[str, Any]:
    """
    Find or create the dataset metadata row.

    Returns a dictionary containing:
        dataset_id
        action: "skip", "retry", or "new"

    Important:
    - Lookup is by file_hash.
    - Loaded datasets are skipped.
    - Pending/failed datasets are retried without deleting anything.
    - New datasets are inserted as pending.
    - This function commits independently from the later import transaction.
    """
    conn = _get_connection()

    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        d.id,
                        d.status,
                        d.source_id,
                        d.dataset_year,
                        d.subject_area,
                        d.file_name,
                        d.file_hash
                    FROM datasets AS d
                    WHERE d.file_hash = %s
                    """,
                    (file_hash,),
                )

                existing = cur.fetchone()

                if existing is not None:
                    dataset_id, status, *_ = existing

                    if status == "loaded":
                        return {
                            "dataset_id": dataset_id,
                            "action": "skip",
                        }

                    if status in ("pending", "failed"):
                        return {
                            "dataset_id": dataset_id,
                            "action": "retry",
                        }

                    raise ValueError(
                        f"Dataset {dataset_id} has unexpected status: {status!r}"
                    )

                cur.execute(
                    """
                    SELECT id
                    FROM sources
                    WHERE code = %s
                    """,
                    (source_code,),
                )

                source_row = cur.fetchone()

                if source_row is None:
                    raise ValueError(
                        f"Unknown source_code: {source_code!r}"
                    )

                source_id = source_row[0]

                cur.execute(
                    """
                    INSERT INTO datasets (
                        source_id,
                        dataset_year,
                        subject_area,
                        file_name,
                        file_hash,
                        status
                    )
                    VALUES (%s, %s, %s, %s, %s, 'pending')
                    RETURNING id
                    """,
                    (
                        source_id,
                        dataset_year,
                        subject_area,
                        file_name,
                        file_hash,
                    ),
                )

                dataset_id = cur.fetchone()[0]

                return {
                    "dataset_id": dataset_id,
                    "action": "new",
                }

    finally:
        conn.close()


def run_dataset_import(
    dataset_id: int,
    import_fn: Callable[[Any], int | None],
) -> None:
    """
    Run one dataset import inside one database transaction.

    import_fn(cursor) performs ALL raw/normalized inserts using the supplied
    cursor and should return the number of successfully imported normalized
    records.

    Success:
        - mark dataset loaded
        - store record_count
        - commit everything

    Failure:
        - roll back all work from the import transaction
        - in a new transaction mark dataset failed
        - re-raise the original exception

    The cursor supplied to import_fn belongs to this transaction. Therefore
    every insert performed by import_fn(cursor) participates in the same
    atomic commit/rollback boundary.
    """
    conn = _get_connection()

    try:
        try:
            with conn:
                with conn.cursor() as cur:
                    # Verify that the dataset exists before starting work.
                    cur.execute(
                        """
                        SELECT id
                        FROM datasets
                        WHERE id = %s
                        FOR UPDATE
                        """,
                        (dataset_id,),
                    )

                    if cur.fetchone() is None:
                        raise ValueError(
                            f"Dataset {dataset_id} does not exist"
                        )

                    record_count = import_fn(cur)

                    if record_count is None:
                        record_count = 0

                    cur.execute(
                        """
                        UPDATE datasets
                        SET
                            status = 'loaded',
                            record_count = %s
                        WHERE id = %s
                        """,
                        (record_count, dataset_id),
                    )

        except Exception:
            # The transaction context above rolls back all inserts made
            # through the supplied cursor.
            #
            # The connection is then reused for a separate transaction to
            # durably record the failed dataset state.
            try:
                conn.rollback()
            except Exception:
                pass

            try:
                with conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE datasets
                            SET status = 'failed'
                            WHERE id = %s
                            """,
                            (dataset_id,),
                        )
            except Exception:
                # Never hide the original import exception.
                pass

            raise

    finally:
        conn.close()


def reset_dataset(
    dataset_id: int,
    normalized_table_name: str,
) -> None:
    """
    MANUAL-ONLY recovery utility.

    Deletes normalized rows for the specified dataset, their dependent
    staging rows, raw rows, and raw file metadata, then resets the dataset
    status to pending.

    This function must NEVER be called automatically by normal ingestion
    retry logic.
    """
    if normalized_table_name not in _ALLOWED_NORMALIZED_TABLES:
        allowed = ", ".join(sorted(_ALLOWED_NORMALIZED_TABLES))
        raise ValueError(
            f"Unsupported normalized table {normalized_table_name!r}. "
            f"Expected one of: {allowed}"
        )

    conn = _get_connection()

    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id
                    FROM datasets
                    WHERE id = %s
                    FOR UPDATE
                    """,
                    (dataset_id,),
                )

                if cur.fetchone() is None:
                    raise ValueError(
                        f"Dataset {dataset_id} does not exist"
                    )

                cur.execute(
                    """
                    SELECT id
                    FROM raw_files
                    WHERE dataset_id = %s
                    """,
                    (dataset_id,),
                )

                raw_file_ids = [row[0] for row in cur.fetchall()]

                raw_row_ids: list[int] = []

                if raw_file_ids:
                    cur.execute(
                        """
                        SELECT id
                        FROM raw_rows
                        WHERE raw_file_id = ANY(%s)
                        """,
                        (raw_file_ids,),
                    )
                    raw_row_ids = [row[0] for row in cur.fetchall()]

                if normalized_table_name == "scimago_records":
                    cur.execute(
                        """
                        DELETE FROM scimago_categories
                        WHERE scimago_record_id IN (
                            SELECT id
                            FROM scimago_records
                            WHERE dataset_id = %s
                        )
                        """,
                        (dataset_id,),
                    )

                    cur.execute(
                        """
                        DELETE FROM scimago_areas
                        WHERE scimago_record_id IN (
                            SELECT id
                            FROM scimago_records
                            WHERE dataset_id = %s
                        )
                        """,
                        (dataset_id,),
                    )

                cur.execute(
                    f"""
                    DELETE FROM {normalized_table_name}
                    WHERE dataset_id = %s
                    """,
                    (dataset_id,),
                )

                if raw_row_ids:
                    cur.execute(
                        """
                        DELETE FROM raw_rows
                        WHERE id = ANY(%s)
                        """,
                        (raw_row_ids,),
                    )

                if raw_file_ids:
                    cur.execute(
                        """
                        DELETE FROM raw_files
                        WHERE id = ANY(%s)
                        """,
                        (raw_file_ids,),
                    )

                cur.execute(
                    """
                    UPDATE datasets
                    SET
                        status = 'pending',
                        record_count = NULL
                    WHERE id = %s
                    """,
                    (dataset_id,),
                )

    finally:
        conn.close()