"""
Shared CSV loading and hashing utilities for JournalHub.

The loader is responsible for reading source files into pandas.
It does not normalize source values.

Raw-row hashing must operate on the original source-row representation
before any source-specific normalization.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def _canonicalize_value(value: Any) -> Any:
    """
    Convert a source-row value into a JSON-serializable canonical value.

    Ordering of checks is deliberate:
    1. pandas missing values -> None
    2. numpy scalar -> native Python scalar
    3. pandas.Timestamp -> ISO-8601 string
    4. ordinary Python scalar passes through
    """
    # Missing values must be checked first.
    #
    # This catches None, NaN, NaT, and pandas scalar missing values.
    if value is None:
        return None

    try:
        missing = pd.isna(value)

        # pd.isna() normally returns a bool for scalar cell values.
        if isinstance(missing, (bool, np.bool_)) and bool(missing):
            return None
    except (TypeError, ValueError):
        pass

    # pandas Timestamp must become an ISO-8601 string.
    if isinstance(value, pd.Timestamp):
        return value.isoformat()

    # numpy scalar types must become native Python scalars.
    if isinstance(value, np.generic):
        return value.item()

    # Plain Python values pass through unchanged.
    return value


def _canonicalize_row(row: dict) -> dict:
    """
    Canonicalize every value in a row dictionary.

    Keys are retained as supplied here; JSON serialization with
    sort_keys=True handles deterministic key ordering.
    """
    return {
        key: _canonicalize_value(value)
        for key, value in row.items()
    }


def serialize_json_value(value: Any) -> Any:
    """
    Convert a source value into a JSON-safe representation.

    Missing pandas/numpy values become None.
    Numpy scalars become native Python values.
    pandas.Timestamp becomes ISO-8601 text.

    This does not perform domain normalization.
    """
    return _canonicalize_value(value)


def serialize_json_row(row: dict) -> dict:
    """
    Convert an untouched source-row dictionary into a JSON-safe dictionary.

    This is representation-level serialization only. It does not apply
    source/domain normalization.
    """
    return _canonicalize_row(row)


def load_csv(
    path,
    delimiter=",",
    encoding="utf-8",
) -> pd.DataFrame:
    """
    Load a CSV file into a pandas DataFrame.

    Parameters:
        path:
            Path to the CSV file.

        delimiter:
            Explicit delimiter. The project uses ';' explicitly for
            SCImago. If None is supplied, delimiter sniffing is used.

        encoding:
            Preferred encoding. UTF-8 is the default.

    Behavior:
        - Try the requested encoding first.
        - If UTF-8 decoding fails, fall back to latin-1.
        - If delimiter is None, use csv.Sniffer to detect it.
        - Return the DataFrame without applying normalization.

    Important:
        This function does not alter raw source values into normalized
        values. Normalization happens later in the source-specific
        transformation layer.
    """
    path = Path(path)

    # Encoding fallback is specifically required for UnicodeDecodeError.
    selected_encoding = encoding

    try:
        if delimiter is None:
            with path.open(
                "r",
                encoding=selected_encoding,
                newline="",
            ) as handle:
                sample = handle.read(8192)

            detected_delimiter = csv.Sniffer().sniff(
                sample,
                delimiters=",;\t|",
            ).delimiter

            return pd.read_csv(
                path,
                delimiter=detected_delimiter,
                encoding=selected_encoding,
            )

        return pd.read_csv(
            path,
            delimiter=delimiter,
            encoding=selected_encoding,
        )

    except UnicodeDecodeError:
        # The frozen plan explicitly requires latin-1 fallback.
        selected_encoding = "latin-1"

        if delimiter is None:
            with path.open(
                "r",
                encoding=selected_encoding,
                newline="",
            ) as handle:
                sample = handle.read(8192)

            detected_delimiter = csv.Sniffer().sniff(
                sample,
                delimiters=",;\t|",
            ).delimiter

            return pd.read_csv(
                path,
                delimiter=detected_delimiter,
                encoding=selected_encoding,
            )

        return pd.read_csv(
            path,
            delimiter=delimiter,
            encoding=selected_encoding,
        )


def compute_sha256(path) -> str:
    """
    Compute the SHA-256 digest of a file.

    The file is read in binary chunks so this works for files much larger
    than available memory.
    """
    path = Path(path)
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)

            if not chunk:
                break

            digest.update(chunk)

    return digest.hexdigest()


def compute_row_hash(row: dict) -> str:
    """
    Compute the deterministic source-row hash.

    Canonicalization:
        1. pandas missing values become None.
        2. numpy scalar values become native Python scalars.
        3. pandas.Timestamp becomes ISO-8601 text.
        4. ordinary Python bool/str/int/float pass through unchanged.
        5. JSON uses sorted keys, compact separators, ensure_ascii=True.
        6. UTF-8 encode the JSON.
        7. SHA-256 and return hexadecimal digest.

    This function intentionally performs NO domain normalization.

    Therefore:
        "1" and "1.0"
    remain different source representations and can produce different
    hashes.
    """
    canonical_row = _canonicalize_row(row)

    serialized = json.dumps(
        canonical_row,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )

    return hashlib.sha256(
        serialized.encode("utf-8")
    ).hexdigest()