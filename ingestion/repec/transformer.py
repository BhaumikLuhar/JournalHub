from __future__ import annotations

from typing import Any

import pandas as pd

from ingestion.common.csv_loader import compute_row_hash
from ingestion.common.normalization import (
    normalize_title,
    parse_int_safe,
    strip_repec_publisher_suffix,
)


NUMERIC_FLOAT_FIELDS = (
    "score",
    "simple_if",
    "recursive_if",
    "discounted_if",
    "recursive_discounted_if",
    "euclid",
)


def _parse_float_safe(
    value: Any,
) -> float | None:
    """
    Parse a RePEc numeric value using ordinary float() semantics.

    RePEc uses standard dot-decimal notation, for example:
        1.53
        3.32
        7.06

    This deliberately does NOT use parse_scimago_decimal(), because
    that parser is specific to SCImago's comma-decimal format.
    """

    if value is None:
        return None

    if isinstance(value, str):
        value = value.strip()

        if not value:
            return None

    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Invalid RePEc numeric value: {value!r}"
        ) from exc


def _transform_repec_row(
    original_row_dict: dict[str, Any],
) -> dict[str, Any]:
    """
    Transform one original RePEc source row.

    The source-row hash is computed from the untouched original row
    dictionary before domain normalization.
    """

    if not isinstance(original_row_dict, dict):
        raise TypeError(
            "_transform_repec_row() expects a dictionary"
        )

    source_row_hash = compute_row_hash(
        original_row_dict
    )

    journal_name_raw = normalize_title(
        original_row_dict.get("journals")
    )

    (
        journal_name_clean,
        publisher_from_name,
        publisher_split_confidence,
    ) = strip_repec_publisher_suffix(
        journal_name_raw
    )

    record: dict[str, Any] = {
        "journal_name_raw": journal_name_raw,
        "journal_name_clean": journal_name_clean,
        "publisher_from_name": publisher_from_name,
        "publisher_split_confidence": publisher_split_confidence,
        "rank": parse_int_safe(
            original_row_dict.get("rank")
        ),
        "score": _parse_float_safe(
            original_row_dict.get("score")
        ),
        "items_listed": parse_int_safe(
            original_row_dict.get("items_listed")
        ),
        "simple_if": _parse_float_safe(
            original_row_dict.get("simple_if")
        ),
        "recursive_if": _parse_float_safe(
            original_row_dict.get("recursive_if")
        ),
        "discounted_if": _parse_float_safe(
            original_row_dict.get("discounted_if")
        ),
        "recursive_discounted_if": _parse_float_safe(
            original_row_dict.get("recursive_discounted_if")
        ),
        "h_index": parse_int_safe(
            original_row_dict.get("h_index")
        ),
        "euclid": _parse_float_safe(
            original_row_dict.get("euclid")
        ),
        "source_snapshot_date": None,
        "source_row_hash": source_row_hash,
    }

    return record


def transform_repec_row(
    original_row_dict: dict[str, Any],
) -> dict[str, Any]:
    """
    Public wrapper for transforming one RePEc source row.

    Kept separate from the private implementation so the ingestion
    pipeline can transform exactly the same original row that it stores
    in raw_rows.
    """

    return _transform_repec_row(
        original_row_dict
    )


def transform_repec(
    df: pd.DataFrame,
) -> list[dict[str, Any]]:
    """
    Transform a complete RePEc DataFrame into normalized records.

    RePEc is one-to-one at the source-row level:
        one source row -> one repec_records row.
    """

    if not isinstance(df, pd.DataFrame):
        raise TypeError(
            "transform_repec() expects a pandas DataFrame"
        )

    records: list[dict[str, Any]] = []

    for _, row in df.iterrows():
        records.append(
            transform_repec_row(
                row.to_dict()
            )
        )

    return records