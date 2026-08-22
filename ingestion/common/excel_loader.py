"""
Shared Excel loading utilities for JournalHub.

These utilities are used for source workbooks such as ABDC where the
actual table header can occur at different rows on different sheets.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ingestion.common.normalization import clean_whitespace


def _normalized_cell(value) -> str:
    """
    Convert a header cell to a comparison-safe representation.

    Header detection is intentionally limited to:
    - string conversion
    - leading/trailing whitespace removal
    - case-folding

    We do not apply title normalization or punctuation stripping here.
    """
    if pd.isna(value):
        return ""

    return str(value).strip().casefold()


def find_header_row(
    sheet,
    candidate_labels: list[str],
    max_scan_rows: int = 15,
) -> int:
    """
    Find the real table header row in an Excel sheet.

    A row is accepted only when BOTH conditions are satisfied:

    (a) At least one cell matches one of candidate_labels after
        strip() + case-fold().

    (b) At least one column in the same row looks like an ISSN,
        rating, or ranking column.

    ISSN detection:
        case-insensitive substring "issn"

    Rating/ranking detection:
        case-insensitive substring "rating" or "ranking"

    Raises:
        ValueError if no row in the scan window satisfies both signals.
    """
    if not candidate_labels:
        raise ValueError(
            "candidate_labels must contain at least one header label"
        )

    normalized_candidates = {
        str(label).strip().casefold()
        for label in candidate_labels
    }

    # max_scan_rows represents the number of rows to inspect starting
    # from row zero.
    scan_limit = min(max_scan_rows, len(sheet))

    for row_number in range(scan_limit):
        row_values = sheet.iloc[row_number].tolist()

        normalized_values = [
            _normalized_cell(value)
            for value in row_values
        ]

        has_candidate_label = any(
            value in normalized_candidates
            for value in normalized_values
        )

        if not has_candidate_label:
            continue

        has_identifier_or_rating_column = any(
            (
                "issn" in value
                or "rating" in value
                or "ranking" in value
            )
            for value in normalized_values
        )

        if has_identifier_or_rating_column:
            return row_number

    labels_display = ", ".join(
        repr(label)
        for label in candidate_labels
    )

    raise ValueError(
        "Could not find a valid Excel header row within the first "
        f"{max_scan_rows} rows. The row must contain one of "
        f"[{labels_display}] and an ISSN/rating/ranking column."
    )


def load_sheet(
    path,
    sheet_name,
    header_row,
) -> pd.DataFrame:
    """
    Load an Excel worksheet using a known header row.

    After loading:
    - every column name is whitespace-cleaned
    - every string cell is whitespace-cleaned

    This produces the normalized representation used by downstream
    source-specific parsing.

    Raw workbook values are not modified by this function.
    """
    path = Path(path)

    dataframe = pd.read_excel(
        path,
        sheet_name=sheet_name,
        header=header_row,
    )

    # Clean column names.
    dataframe.columns = [
        clean_whitespace(column)
        for column in dataframe.columns
    ]

    # Clean string cells only.
    #
    # Non-string values such as integers, floats, NaN, and timestamps
    # remain unchanged.
    for column in dataframe.columns:
        dataframe[column] = dataframe[column].map(
            lambda value: (
                clean_whitespace(value)
                if isinstance(value, str)
                else value
            )
        )

    return dataframe