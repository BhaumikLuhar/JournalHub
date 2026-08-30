from __future__ import annotations

from typing import Any

import pandas as pd

from ingestion.abdc.parser import (
    FOR_SCHEME_BY_YEAR,
    RATING_COLUMN_BY_SHEET,
    SHEET_YEARS,
)
from ingestion.common.csv_loader import compute_row_hash
from ingestion.common.normalization import (
    clean_whitespace,
    first_non_empty,
    normalize_abdc_rating,
    normalize_issn,
    normalize_title,
    parse_year,
)


TITLE_COLUMNS = [
    "Journal Title",
    "Journal Name",
]

PUBLISHER_COLUMNS = [
    "Publisher",
]

ISSN_COLUMNS = [
    "ISSN",
]

ONLINE_ISSN_COLUMNS = [
    "ISSN Online",
    "ISSNOnline",
]

INCEPTION_YEAR_COLUMNS = [
    "Year Inception",
    "Start year",
]

FOR_COLUMNS = [
    "FoR",
    "ABDC FoR code",
    "Field of Research",
]


def transform_row(
    row: dict[str, Any] | pd.Series,
    *,
    sheet_name: str,
) -> dict[str, Any]:
    """
    Transform one untouched ABDC source row into a normalized record.

    The original source row is never mutated.

    source_row_hash is calculated from the original row representation
    before domain normalization.
    """

    if sheet_name not in SHEET_YEARS:
        supported = ", ".join(SHEET_YEARS)

        raise ValueError(
            f"Unsupported ABDC sheet {sheet_name!r}. "
            f"Expected one of: {supported}"
        )

    # Keep the original source representation intact.
    original_row = (
        row.to_dict()
        if isinstance(row, pd.Series)
        else dict(row)
    )

    rating_year = SHEET_YEARS[sheet_name]

    try:
        for_scheme = FOR_SCHEME_BY_YEAR[rating_year]
    except KeyError as exc:
        raise ValueError(
            f"No FoR scheme configured for ABDC rating year "
            f"{rating_year}"
        ) from exc

    # ---------------------------------------------------------
    # Source-row hash
    # ---------------------------------------------------------
    #
    # IMPORTANT:
    # Hash the original row before normalization.
    # ---------------------------------------------------------
    source_row_hash = compute_row_hash(original_row)

    # ---------------------------------------------------------
    # Journal name
    # ---------------------------------------------------------
    #
    # 2025–2016 use "Journal Title".
    # 2013–2010 use "Journal Name".
    # first_non_empty() safely handles the historical difference.
    # ---------------------------------------------------------
    raw_journal_name = first_non_empty(
        original_row,
        TITLE_COLUMNS,
    )

    journal_name = normalize_title(
        raw_journal_name
    )

    # ---------------------------------------------------------
    # Publisher
    # ---------------------------------------------------------
    #
    # The 2010 sheet has no Publisher column.
    # first_non_empty() therefore returns None.
    # ---------------------------------------------------------
    raw_publisher = first_non_empty(
        original_row,
        PUBLISHER_COLUMNS,
    )

    publisher = (
        clean_whitespace(str(raw_publisher))
        if raw_publisher is not None
        else None
    )

    # ---------------------------------------------------------
    # Print ISSN
    # ---------------------------------------------------------
    raw_issn = first_non_empty(
        original_row,
        ISSN_COLUMNS,
    )

    issn = normalize_issn(
        raw_issn
    )

    # ---------------------------------------------------------
    # Online ISSN
    # ---------------------------------------------------------
    #
    # Historical spellings:
    #   "ISSN Online"
    #   "ISSNOnline"
    # ---------------------------------------------------------
    raw_issn_online = first_non_empty(
        original_row,
        ONLINE_ISSN_COLUMNS,
    )

    issn_online = normalize_issn(
        raw_issn_online
    )

    # ---------------------------------------------------------
    # Inception / start year
    # ---------------------------------------------------------
    raw_year_inception = first_non_empty(
        original_row,
        INCEPTION_YEAR_COLUMNS,
    )

    year_inception = parse_year(
        raw_year_inception
    )

    # ---------------------------------------------------------
    # Field of Research code
    # ---------------------------------------------------------
    #
    # Historical spellings:
    #   "FoR"
    #   "Field of Research"
    #   "ABDC FoR code"
    # ---------------------------------------------------------
    raw_for_code = first_non_empty(
        original_row,
        FOR_COLUMNS,
    )

    for_code = (
        clean_whitespace(str(raw_for_code))
        if raw_for_code is not None
        else None
    )

    # ---------------------------------------------------------
    # Rating
    # ---------------------------------------------------------
    #
    # The rating column changes by historical worksheet.
    #
    # normalize_abdc_rating() handles:
    #   "A*" -> "A*"
    #   "A"  -> "A"
    #   "B"  -> "B"
    #   "C"  -> "C"
    #   "c"  -> "C"
    #   blank/NaN -> None
    # ---------------------------------------------------------
    rating_column = RATING_COLUMN_BY_SHEET[sheet_name]

    raw_rating = first_non_empty(
        original_row,
        [rating_column],
    )

    rating = normalize_abdc_rating(
        raw_rating
    )

    return {
        "source_row_hash": source_row_hash,
        "rating_year": rating_year,
        "journal_name": journal_name,
        "publisher": publisher,
        "issn": issn,
        "issn_online": issn_online,
        "year_inception": year_inception,
        "for_code": for_code,
        "for_scheme": for_scheme,
        "rating": rating,
    }


def transform_dataframe(
    dataframe: pd.DataFrame,
    *,
    sheet_name: str,
) -> list[dict[str, Any]]:
    """
    Transform every row in an already-parsed ABDC worksheet.

    No database operations are performed.
    """

    records: list[dict[str, Any]] = []

    for _, row in dataframe.iterrows():
        records.append(
            transform_row(
                row,
                sheet_name=sheet_name,
            )
        )

    return records