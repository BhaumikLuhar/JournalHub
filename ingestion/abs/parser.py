from __future__ import annotations

from pathlib import Path

import pandas as pd

from ingestion.common.csv_loader import load_csv


def read_abs_csv(path) -> pd.DataFrame:
    """
    Read an ABS AJG CSV file.

    ABS uses a standard comma delimiter.

    This function is intentionally responsible only for loading the
    source CSV. It does not normalize titles, ISSNs, publishers, or
    ratings.
    """

    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(
            f"ABS CSV file does not exist: {path}"
        )

    if not path.is_file():
        raise ValueError(
            f"ABS CSV path is not a file: {path}"
        )

    return load_csv(
        path,
        delimiter=",",
    )