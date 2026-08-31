from __future__ import annotations

from pathlib import Path

import pandas as pd

from ingestion.common.csv_loader import load_csv


EXPECTED_COLUMNS = [
    "rank",
    "journal_name",
    "ft50_year",
]


def read_ft50_csv(
    path: str | Path,
) -> pd.DataFrame:
    """
    Read the FT50 source CSV without applying domain normalization.

    FT50 is a standard comma-delimited CSV.

    The parser validates the exact expected source columns because the
    Day 1 inventory established the FT50 schema as:

        rank, journal_name, ft50_year

    Raw source values are returned unchanged by this parser.
    """

    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(
            f"Expected FT50 CSV file does not exist: {path}"
        )

    if not path.is_file():
        raise ValueError(
            f"Expected FT50 CSV path is not a file: {path}"
        )

    dataframe = load_csv(
        path,
        delimiter=",",
    )

    actual_columns = [
        str(column)
        for column in dataframe.columns
    ]

    if actual_columns != EXPECTED_COLUMNS:
        raise ValueError(
            "Unexpected FT50 columns. "
            f"Expected {EXPECTED_COLUMNS!r}, "
            f"got {actual_columns!r}"
        )

    return dataframe