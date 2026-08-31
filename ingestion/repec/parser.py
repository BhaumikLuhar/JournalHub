from __future__ import annotations

from pathlib import Path

import pandas as pd

from ingestion.common.csv_loader import load_csv


def read_repec_csv(path) -> pd.DataFrame:
    """
    Read a RePEc CSV file.

    RePEc uses a standard comma delimiter.

    This function is responsible only for loading the source CSV.
    No title normalization, publisher splitting, numeric parsing,
    or other domain transformation is performed here.
    """

    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(
            f"RePEc CSV file does not exist: {path}"
        )

    if not path.is_file():
        raise ValueError(
            f"RePEc CSV path is not a file: {path}"
        )

    return load_csv(
        path,
        delimiter=",",
    )