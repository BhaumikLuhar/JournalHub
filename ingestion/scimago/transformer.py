"""SCImago source-row transformation utilities for JournalHub.

This module converts an untouched SCImago source row into the normalized
representation required by the staging tables.

Important:
    - No database writes happen here.
    - The original source-row dictionary is never normalized in-place.
    - source_row_hash is calculated from the original source representation.
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

from ingestion.common.csv_loader import compute_row_hash
from ingestion.common.normalization import (
    clean_whitespace,
    normalize_scimago_quartile,
    normalize_title,
    parse_int_safe,
    parse_scimago_decimal,
    split_multi_issn,
)
from ingestion.common.validation import is_in_range


_CATEGORY_PATTERN = re.compile(r"^(.*)\s\((Q[1-4])\)$")


def _is_missing(value: Any) -> bool:
    """Return True when a pandas/source value is missing or blank."""
    if value is None:
        return True

    if isinstance(value, str):
        return value.strip() == ""

    try:
        result = pd.isna(value)

        if isinstance(result, bool):
            return result

        if hasattr(result, "item"):
            return bool(result.item())

        return False
    except (TypeError, ValueError):
        return False


def _string_or_none(value: Any) -> str | None:
    """Return a cleaned string or None for a missing source value."""
    if _is_missing(value):
        return None

    return clean_whitespace(str(value))


def _parse_boolean_yes(value: Any) -> bool:
    """Return True only when the source value is exactly 'yes'."""
    if _is_missing(value):
        return False

    return str(value).strip().lower() == "yes"


def parse_categories(raw: Any) -> list[dict[str, str | None]]:
    """Parse the SCImago Categories field.

    Example:
        "History and Philosophy of Science (Q1); Multidisciplinary (Q1)"

    becomes:

        [
            {
                "category_name": "History and Philosophy of Science",
                "quartile": "Q1",
            },
            {
                "category_name": "Multidisciplinary",
                "quartile": "Q1",
            },
        ]

    Unexpected category formatting is retained with a None quartile rather
    than guessed.
    """
    if _is_missing(raw):
        return []

    categories: list[dict[str, str | None]] = []

    for piece in str(raw).split(";"):
        piece = piece.strip()

        if not piece:
            continue

        match = _CATEGORY_PATTERN.match(piece)

        if match:
            category_name = clean_whitespace(match.group(1))
            quartile = match.group(2)
        else:
            category_name = clean_whitespace(piece)
            quartile = None

        if category_name:
            categories.append(
                {
                    "category_name": category_name,
                    "quartile": quartile,
                }
            )

    return categories


def parse_areas(raw: Any) -> list[str]:
    """Parse the SCImago Areas field into cleaned area names."""
    if _is_missing(raw):
        return []

    areas: list[str] = []

    for piece in str(raw).split(";"):
        area_name = clean_whitespace(piece)

        if area_name:
            areas.append(area_name)

    return areas


def transform_row(
    row: dict[str, Any],
    *,
    year: int,
    subject_area: str,
) -> dict[str, Any]:
    """Transform one untouched SCImago source row.

    Parameters:
        row:
            Original source-row dictionary. This dictionary is never mutated.

        year:
            Year parsed from the SCImago filename.

        subject_area:
            Subject area parsed from the SCImago filename.

    Returns:
        A normalized SCImago record dictionary.

    Notes:
        The returned dictionary contains ``sjr_out_of_range`` so the pipeline
        can reject that source row without the transformer performing a
        database write.
    """
    original_row = dict(row)

    total_docs_column = f"Total Docs. ({year})"

    sjr = parse_scimago_decimal(row.get("SJR"))

    categories = parse_categories(row.get("Categories"))
    areas = parse_areas(row.get("Areas"))

    transformed = {
        "year": year,
        "subject_area": clean_whitespace(subject_area),

        "rank": parse_int_safe(row.get("Rank")),
        "sourceid": clean_whitespace(str(row.get("Sourceid", ""))),
        "title": normalize_title(row.get("Title")),

        "type": _string_or_none(row.get("Type")),
        "issn_list": split_multi_issn(row.get("Issn")),
        "issn_raw": _string_or_none(row.get("Issn")),

        "publisher_raw": _string_or_none(row.get("Publisher")),

        "open_access": _parse_boolean_yes(row.get("Open Access")),
        "open_access_diamond": _parse_boolean_yes(
            row.get("Open Access Diamond")
        ),

        "sjr": sjr,
        "sjr_best_quartile": normalize_scimago_quartile(
            row.get("SJR Best Quartile")
        ),

        "h_index": parse_int_safe(row.get("H index")),
        "total_docs": parse_int_safe(row.get(total_docs_column)),

        "total_docs_3years": parse_int_safe(
            row.get("Total Docs. (3years)")
        ),
        "total_refs": parse_int_safe(row.get("Total Refs")),
        "total_citations_3years": parse_int_safe(
            row.get("Total Citations (3years)")
        ),
        "citable_docs_3years": parse_int_safe(
            row.get("Citable Docs. (3years)")
        ),

        "citations_per_doc_2years": parse_scimago_decimal(
            row.get("Citations / Doc. (2years)")
        ),
        "refs_per_doc": parse_scimago_decimal(
            row.get("Ref. / Doc.")
        ),
        "female_percentage": parse_scimago_decimal(
            row.get("%Female")
        ),

        "overton": parse_int_safe(row.get("Overton")),

        "country": _string_or_none(row.get("Country")),
        "region": _string_or_none(row.get("Region")),
        "coverage": _string_or_none(row.get("Coverage")),

        "categories": categories,
        "areas": areas,

        "source_row_hash": compute_row_hash(original_row),

        "sjr_out_of_range": (
            not is_in_range(sjr, 0, 100)
            if sjr is not None
            else False
        ),
    }

    return transformed