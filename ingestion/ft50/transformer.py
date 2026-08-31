from __future__ import annotations

from typing import Any

import pandas as pd

from ingestion.common.csv_loader import compute_row_hash
from ingestion.common.normalization import (
    normalize_title,
    parse_int_safe,
)


EXPECTED_COLUMNS = (
    "rank",
    "journal_name",
    "ft50_year",
)


def _validate_original_row(
    original_row_dict: dict[str, Any],
) -> None:
    """
    Validate that the original source row contains the FT50 columns.

    This checks structure only. It does not normalize source values.
    """

    if not isinstance(original_row_dict, dict):
        raise TypeError(
            "_transform_ft50_row() expects a dictionary"
        )

    missing_columns = [
        column
        for column in EXPECTED_COLUMNS
        if column not in original_row_dict
    ]

    if missing_columns:
        raise ValueError(
            "FT50 source row is missing required columns: "
            f"{missing_columns!r}"
        )


def _transform_ft50_row(
    original_row_dict: dict[str, Any],
) -> dict[str, Any]:
    """
    Transform one untouched FT50 source row.

    The source-row hash is computed BEFORE any domain normalization.

    FT50 contains only journals that are included in the list, so
    `included` is always True.

    Returns the normalized representation expected by ft50_records.
    """

    _validate_original_row(
        original_row_dict
    )

    # -------------------------------------------------------------
    # IMPORTANT:
    # Compute lineage from the untouched source representation.
    # Do this before normalize_title() or parse_int_safe().
    # -------------------------------------------------------------
    source_row_hash = compute_row_hash(
        original_row_dict
    )

    journal_name = normalize_title(
        original_row_dict.get("journal_name")
    )

    record: dict[str, Any] = {
        "rank": parse_int_safe(
            original_row_dict.get("rank")
        ),
        "journal_name": journal_name,
        "ft50_year": parse_int_safe(
            original_row_dict.get("ft50_year")
        ),
        "included": True,
        "source_row_hash": source_row_hash,
    }

    return record


def transform_ft50_row(
    original_row_dict: dict[str, Any],
) -> dict[str, Any]:
    """
    Public wrapper for transforming one FT50 source row.

    The input must be the exact original source-row dictionary used for
    raw-row lineage storage.
    """

    return _transform_ft50_row(
        original_row_dict
    )


def transform_ft50(
    df: pd.DataFrame,
) -> list[dict[str, Any]]:
    """
    Transform a complete FT50 DataFrame.

    FT50 is one-to-one at source-row level:

        one source row -> one ft50_records row
    """

    if not isinstance(df, pd.DataFrame):
        raise TypeError(
            "transform_ft50() expects a pandas DataFrame"
        )

    records: list[dict[str, Any]] = []

    for _, row in df.iterrows():
        records.append(
            transform_ft50_row(
                row.to_dict()
            )
        )

    return records