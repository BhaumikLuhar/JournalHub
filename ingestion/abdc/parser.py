from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from ingestion.common.excel_loader import (
    find_header_row,
    load_sheet,
)


# ---------------------------------------------------------------------------
# ABDC workbook structure
# ---------------------------------------------------------------------------
#
# The ABDC workbook contains six rating-list sheets.  The sheet names are
# stable source metadata, while the actual Excel header row varies between
# sheets.  Header positions therefore MUST NOT be hardcoded here.
#
# The parser uses find_header_row() to discover the real header dynamically.
# ---------------------------------------------------------------------------

SHEET_YEARS: dict[str, int] = {
    "2025 JQL": 2025,
    "2022 JQL": 2022,
    "2019 JQL": 2019,
    "2016 JQL": 2016,
    "2013 JQL": 2013,
    "2010 JQL": 2010,
}


# ABDC changed the underlying Field of Research classification between
# the 2008 and 2020 Australian and New Zealand Standard Research
# Classification schemes.
FOR_SCHEME_BY_YEAR: dict[int, str] = {
    2025: "ANZSRC2020",
    2022: "ANZSRC2008",
    2019: "ANZSRC2008",
    2016: "ANZSRC2008",
    2013: "ANZSRC2008",
    2010: "ANZSRC2008",
}


# The rating column name is source-specific and changed over the historical
# ABDC workbook versions.
#
# These are matched after the Excel loader has whitespace-cleaned column
# names.  Case differences are handled by the column lookup helper below.
RATING_COLUMN_BY_SHEET: dict[str, str] = {
    "2025 JQL": "2025 rating",
    "2022 JQL": "2022 rating",
    "2019 JQL": "2019 Rating",
    "2016 JQL": "2016 rating",
    "2013 JQL": "ABDC List 2013",
    "2010 JQL": "ABDC Ranking",
}


# Candidate labels used only for dynamic header discovery.
#
# Every valid ABDC sheet must contain one journal-title label and also an
# ISSN/rating/ranking column.  find_header_row() enforces both conditions.
HEADER_CANDIDATE_LABELS: tuple[str, ...] = (
    "Journal Name",
    "Journal Title",
)


DEFAULT_MAX_HEADER_SCAN_ROWS = 15


def get_rating_year(sheet_name: str) -> int:
    """
    Return the ABDC rating year represented by a worksheet.

    Raises:
        ValueError: if the worksheet is not one of the supported ABDC sheets.
    """
    try:
        return SHEET_YEARS[sheet_name]
    except KeyError as exc:
        supported = ", ".join(SHEET_YEARS)
        raise ValueError(
            f"Unsupported ABDC sheet {sheet_name!r}. "
            f"Expected one of: {supported}"
        ) from exc


def get_for_scheme(sheet_name: str) -> str:
    """
    Return the ANZSRC scheme associated with an ABDC worksheet.

    Raises:
        ValueError: if the worksheet is not supported.
    """
    rating_year = get_rating_year(sheet_name)

    try:
        return FOR_SCHEME_BY_YEAR[rating_year]
    except KeyError as exc:
        raise ValueError(
            f"No FoR scheme configured for ABDC rating year "
            f"{rating_year}"
        ) from exc


def get_rating_column(sheet_name: str) -> str:
    """
    Return the expected rating column for an ABDC worksheet.

    Raises:
        ValueError: if the worksheet is not supported.
    """
    try:
        return RATING_COLUMN_BY_SHEET[sheet_name]
    except KeyError as exc:
        supported = ", ".join(RATING_COLUMN_BY_SHEET)
        raise ValueError(
            f"Unsupported ABDC sheet {sheet_name!r}. "
            f"Expected one of: {supported}"
        ) from exc


def _find_column_casefold(
    dataframe: pd.DataFrame,
    expected_name: str,
) -> str:
    """
    Find a dataframe column using whitespace-normalized, case-insensitive
    comparison.

    The Excel loader has already cleaned whitespace from column names.
    Case-insensitive matching is retained here because historical ABDC
    sheets use inconsistent capitalization such as:

        2019 Rating
        2016 rating

    Returns:
        The actual dataframe column name.

    Raises:
        ValueError: if the expected column is missing or ambiguous.
    """
    expected = expected_name.strip().casefold()

    matches = [
        column
        for column in dataframe.columns
        if str(column).strip().casefold() == expected
    ]

    if not matches:
        available = ", ".join(
            repr(str(column))
            for column in dataframe.columns
        )

        raise ValueError(
            f"Required ABDC column {expected_name!r} was not found. "
            f"Available columns: {available}"
        )

    if len(matches) > 1:
        raise ValueError(
            f"ABDC column {expected_name!r} matched multiple columns: "
            f"{matches}"
        )

    return matches[0]


def _validate_required_columns(
    dataframe: pd.DataFrame,
    sheet_name: str,
) -> None:
    """
    Validate that the minimum columns required by later ABDC processing
    exist in the loaded worksheet.

    This is intentionally structural validation only.

    Domain validation belongs in transformer.py / validator.py.
    """
    required_title_columns = {
        str(column).strip().casefold()
        for column in dataframe.columns
    }

    if not (
        "journal name" in required_title_columns
        or "journal title" in required_title_columns
    ):
        available = ", ".join(
            repr(str(column))
            for column in dataframe.columns
        )

        raise ValueError(
            f"ABDC sheet {sheet_name!r} does not contain a journal "
            f"name/title column. Available columns: {available}"
        )

    # Confirm that the sheet-specific rating column actually exists.
    get_rating_column_name = get_rating_column(sheet_name)

    _find_column_casefold(
        dataframe,
        get_rating_column_name,
    )

    # At least one ISSN-bearing column must exist.  The actual source uses
    # slightly different spellings across workbook versions:
    #
    #     ISSN
    #     ISSNOnline
    #     ISSN Online
    #
    # We intentionally identify these structurally rather than hardcoding
    # every historical spelling here.
    issn_columns = [
        column
        for column in dataframe.columns
        if "issn" in str(column).casefold()
    ]

    if not issn_columns:
        available = ", ".join(
            repr(str(column))
            for column in dataframe.columns
        )

        raise ValueError(
            f"ABDC sheet {sheet_name!r} does not contain an ISSN "
            f"column. Available columns: {available}"
        )


def read_abdc_sheet(
    path: str | Path,
    sheet_name: str,
    *,
    max_scan_rows: int = DEFAULT_MAX_HEADER_SCAN_ROWS,
) -> pd.DataFrame:
    """
    Read one ABDC worksheet using dynamically detected headers.

    Parameters:
        path:
            Path to the ABDC XLSX workbook.

        sheet_name:
            One of the six supported ABDC worksheet names.

        max_scan_rows:
            Number of worksheet rows to inspect while searching for the
            real header.  The default of 15 is sufficient for the current
            ABDC workbook and prevents an accidental scan of a large
            worksheet.

    Returns:
        A pandas DataFrame loaded using the detected header row.

    Raises:
        ValueError:
            If the sheet is unsupported, no valid header can be found,
            or required structural columns are missing.

        FileNotFoundError:
            If the workbook path does not exist.

    Notes:
        The function delegates header detection and whitespace cleaning
        to ingestion.common.excel_loader.

        It does not perform domain normalization of journal titles, ISSNs,
        ratings, FoR codes, or years.  Those responsibilities belong to
        the ABDC transformer and validator.
    """
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(
            f"ABDC workbook does not exist: {path}"
        )

    if not path.is_file():
        raise ValueError(
            f"ABDC workbook path is not a file: {path}"
        )

    # Validate sheet configuration before opening the worksheet.
    get_rating_year(sheet_name)
    get_for_scheme(sheet_name)
    get_rating_column(sheet_name)

    # Read only the first max_scan_rows rows without assuming the header
    # position.  header=None preserves the worksheet's actual row layout
    # for find_header_row().
    preview = pd.read_excel(
        path,
        sheet_name=sheet_name,
        header=None,
        nrows=max_scan_rows,
    )

    header_row = find_header_row(
        preview,
        candidate_labels=list(HEADER_CANDIDATE_LABELS),
        max_scan_rows=max_scan_rows,
    )

    dataframe = load_sheet(
        path,
        sheet_name=sheet_name,
        header_row=header_row,
    )

    _validate_required_columns(
        dataframe,
        sheet_name,
    )

    return dataframe


def list_supported_sheets() -> list[str]:
    """
    Return the supported ABDC worksheet names in deterministic order.
    """
    return list(SHEET_YEARS.keys())