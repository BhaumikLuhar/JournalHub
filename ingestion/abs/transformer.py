from __future__ import annotations

import re
from typing import Any

import pandas as pd

from ingestion.common.csv_loader import compute_row_hash
from ingestion.common.normalization import (
    clean_whitespace,
    normalize_issn,
    normalize_title,
)


_RATING_YEAR_PATTERN = re.compile(r"^AJG(\d{4})$")


def _detect_rating_columns(
    row: dict[str, Any],
) -> list[tuple[str, int]]:
    """
    Detect ABS rating-year columns dynamically.

    A valid rating column has the form:

        AJGYYYY

    For example:
        AJG2018
        AJG2021
        AJG2024
        AJG2027
    """

    rating_columns: list[tuple[str, int]] = []

    for column_name in row.keys():
        match = _RATING_YEAR_PATTERN.fullmatch(
            str(column_name)
        )

        if match is None:
            continue

        rating_columns.append(
            (
                str(column_name),
                int(match.group(1)),
            )
        )

    return rating_columns


def _is_blank_rating(value: Any) -> bool:
    """
    Return True when an ABS rating cell is blank or missing.
    """

    if value is None:
        return True

    if isinstance(value, str):
        return value.strip() == ""

    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def transform_abs_row(
    original_row_dict: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Transform one original wide ABS source row into zero or more
    normalized rating-year records.

    The source-row hash is calculated exactly once from the original
    wide row and reused across every emitted rating-year record.

    This helper is used by the ingestion pipeline so that raw_rows
    lineage and normalized records are created from the exact same
    source-row representation.
    """

    if not isinstance(original_row_dict, dict):
        raise TypeError(
            "transform_abs_row() expects a dictionary"
        )

    source_row_hash = compute_row_hash(
        original_row_dict
    )

    rating_columns = _detect_rating_columns(
        original_row_dict
    )

    if not rating_columns:
        raise ValueError(
            "No ABS AJGYYYY rating columns were found "
            "in the source row."
        )

    records: list[dict[str, Any]] = []

    for column_name, rating_year in rating_columns:
        raw_rating = original_row_dict.get(
            column_name
        )

        if _is_blank_rating(raw_rating):
            continue

        records.append(
            {
                "journal_name": normalize_title(
                    original_row_dict.get("TITLE")
                ),
                "field": clean_whitespace(
                    original_row_dict.get("FIELD")
                ),
                "issn": normalize_issn(
                    original_row_dict.get("ISSN")
                ),
                "publisher": clean_whitespace(
                    original_row_dict.get("PUBLISHER")
                ),
                "rating_year": rating_year,
                "rating": clean_whitespace(
                    raw_rating
                ),
                "source_row_hash": source_row_hash,
            }
        )

    return records


def unpivot_abs(
    df: pd.DataFrame,
) -> list[dict[str, Any]]:
    """
    Convert the complete wide ABS DataFrame into normalized long
    records.

    One source row can emit multiple records:
        one record per populated AJGYYYY column.

    The public function delegates each row to transform_abs_row().
    """

    if not isinstance(df, pd.DataFrame):
        raise TypeError(
            "unpivot_abs() expects a pandas DataFrame"
        )

    records: list[dict[str, Any]] = []

    for _, row in df.iterrows():
        records.extend(
            transform_abs_row(
                row.to_dict()
            )
        )

    return records