"""
Shared normalization utilities for JournalHub.

These functions operate on normalized representations of source data.
They must never be used to mutate values destined for raw_data/raw_json.

The untouched files under data/raw/ remain the byte-level source of truth.
"""

from __future__ import annotations

import logging
import math
import re
import unicodedata
from typing import Any

import pandas as pd


logger = logging.getLogger(__name__)


# RePEc publisher-suffix blocklist.
#
# If the segment after the final comma contains one of these words,
# the comma is considered unsafe as a publisher/title boundary.
_REPEC_UNSAFE_SUFFIX_WORDS = (
    "theory",
    "practice",
    "review",
    "studies",
    "perspectives",
    "issues",
    "journal",
    "quarterly",
    "annual",
)


def _is_missing(raw: Any) -> bool:
    """
    Return True when raw represents a missing scalar value.

    This helper is intentionally defensive because values coming from
    pandas/openpyxl may be strings, numeric scalars, None, or NaN.
    """
    if raw is None:
        return True

    if isinstance(raw, str):
        return raw.strip() == ""

    try:
        result = pd.isna(raw)

        # pd.isna() may return an array-like value for non-scalar inputs.
        # These normalization functions expect scalar cell values.
        if isinstance(result, bool):
            return result

        if hasattr(result, "item"):
            return bool(result.item())

        return False
    except (TypeError, ValueError):
        return False


def clean_whitespace(raw: str) -> str:
    """
    Strip leading/trailing whitespace and collapse internal whitespace.

    This is intended for normalized representations only.
    """
    if _is_missing(raw):
        return ""

    text = str(raw).strip()
    return re.sub(r"\s+", " ", text)


def normalize_issn(raw: str) -> str | None:
    """
    Normalize an ISSN.

    Rules:
    - strip whitespace/tabs
    - remove hyphens
    - uppercase
    - require exactly 8 characters
    - first 7 characters must be digits
    - final character must be a digit or X

    Examples:
        "1614-2411\\t" -> "16142411"
        "1533-628X"    -> "1533628X"
        "-"            -> None
    """
    if _is_missing(raw):
        return None

    value = str(raw).strip().replace("-", "").upper()

    if len(value) != 8:
        return None

    if not value[:7].isdigit():
        return None

    if not (value[7].isdigit() or value[7] == "X"):
        return None

    return value


def split_multi_issn(raw: str) -> list[str]:
    """
    Split a multi-ISSN source value and normalize each ISSN.

    SCImago can store multiple ISSNs in one comma-separated cell.

    Invalid/missing ISSNs are silently dropped from the returned list.
    """
    if _is_missing(raw):
        return []

    normalized: list[str] = []

    for value in str(raw).split(","):
        issn = normalize_issn(value)

        if issn is not None:
            normalized.append(issn)

    return normalized


def normalize_title(raw: str) -> str:
    """
    Produce a display-safe normalized title.

    Rules:
    - strip leading/trailing whitespace
    - collapse multiple internal whitespace characters
    - preserve capitalization
    - preserve punctuation
    - preserve the actual wording

    This representation may be stored as canonical_title or alias_name.
    """
    return clean_whitespace(raw)


def _strip_unicode_punctuation(text: str) -> str:
    """
    Remove Unicode punctuation characters while preserving letters/numbers.

    Using Unicode character categories rather than string.punctuation is
    intentional because journal titles can contain non-ASCII characters.
    """
    return "".join(
        character
        for character in text
        if not unicodedata.category(character).startswith("P")
    )


def normalized_matching_title(raw: str) -> str:
    """
    Produce the matching-only title representation.

    Rules:
    - lowercase
    - replace '&' with 'and'
    - remove punctuation
    - collapse whitespace
    - remove common leading articles: the, a, an

    This value must never be displayed as a canonical journal title.
    """
    if _is_missing(raw):
        return ""

    text = str(raw).strip().lower()
    text = text.replace("&", " and ")
    text = _strip_unicode_punctuation(text)
    text = re.sub(r"\s+", " ", text).strip()

    for article in ("the ", "a ", "an "):
        if text.startswith(article):
            text = text[len(article) :]
            break

    return text


def strip_repec_publisher_suffix(
    raw: str,
) -> tuple[str, str | None, str]:
    """
    Split a RePEc journal display name into title and publisher.

    Returns:
        (clean_title, publisher_or_None, confidence)

    Confidence values:
        "high" -> safe comma-based split
        "low"  -> comma exists but suffix looks like title text
        "none" -> no comma exists

    The split is performed at the LAST comma.
    """
    if _is_missing(raw):
        return "", None, "none"

    value = str(raw).strip()

    if "," not in value:
        return value, None, "none"

    before, after = value.rsplit(",", 1)

    before = before.strip()
    after = after.strip()

    if not before or not after:
        return value, None, "low"

    after_lower = after.casefold()

    if any(
        word in after_lower
        for word in _REPEC_UNSAFE_SUFFIX_WORDS
    ):
        return value, None, "low"

    return before, after, "high"


def parse_scimago_decimal(raw: str) -> float | None:
    """
    Parse a SCImago decimal representation.

    SCImago uses comma-as-decimal formatting, e.g.:

        "13,110" -> 13.110
        "30,18"  -> 30.18

    Rules:
    - strip surrounding whitespace
    - if exactly one comma and no period exists, treat comma as decimal mark
    - otherwise attempt normal float conversion
    - return None on failure
    - log a warning instead of raising

    This function is specifically for SCImago.
    RePEc numeric values must use ordinary float parsing instead.
    """
    if _is_missing(raw):
        return None

    value = str(raw).strip()

    if value.count(",") == 1 and "." not in value:
        value = value.replace(",", ".")

    try:
        parsed = float(value)

        if not math.isfinite(parsed):
            logger.warning(
                "SCImago decimal is not finite: %r",
                raw,
            )
            return None

        return parsed

    except (TypeError, ValueError):
        logger.warning(
            "Could not parse SCImago decimal: %r",
            raw,
        )
        return None


def normalize_scimago_quartile(raw: str) -> str | None:
    """
    Normalize a SCImago SJR Best Quartile value.

    "-" and blank values represent no assigned quartile and become None.

    Valid quartiles:
        Q1, Q2, Q3, Q4

    Unexpected values are logged and returned as None.
    """
    if _is_missing(raw):
        return None

    value = str(raw).strip().upper()

    if value == "-" or value == "":
        return None

    if value in {"Q1", "Q2", "Q3", "Q4"}:
        return value

    logger.warning(
        "Unexpected SCImago quartile value: %r",
        raw,
    )
    return None


def normalize_abdc_rating(raw: str) -> str | None:
    """
    Normalize an ABDC rating.

    Valid normalized ratings:
        A*
        A
        B
        C

    Matching is case-insensitive and whitespace-tolerant.

    Blank, NaN, and unrecognized values return None.
    """
    if _is_missing(raw):
        return None

    value = str(raw).strip().upper()

    if value in {"A*", "A", "B", "C"}:
        return value

    return None


def first_non_empty(
    row,
    column_names: list[str],
) -> Any | None:
    """
    Return the first non-empty value found among the supplied columns.

    A value is considered usable when:
    - pandas.notna(value) is True
    - its string representation is not empty after stripping

    This deliberately avoids unsafe expressions such as:
        row["A"] or row["B"]
    """
    for column_name in column_names:
        try:
            value = row[column_name]
        except (KeyError, IndexError, TypeError):
            continue

        if pd.notna(value) and str(value).strip() != "":
            return value

    return None


def parse_int_safe(raw) -> int | None:
    """
    Parse an integer-like value safely.

    Handles:
    - integer values
    - integer-valued floats
    - strings containing surrounding whitespace/tabs

    Returns None for blank, missing, non-numeric, or non-integer values.
    """
    if _is_missing(raw):
        return None

    if isinstance(raw, bool):
        return int(raw)

    try:
        if isinstance(raw, str):
            value = raw.strip()

            if value == "":
                return None

            # Handle strings such as "2003.0" conservatively.
            parsed_float = float(value)

            if not math.isfinite(parsed_float):
                return None

            if not parsed_float.is_integer():
                return None

            return int(parsed_float)

        parsed_float = float(raw)

        if not math.isfinite(parsed_float):
            return None

        if not parsed_float.is_integer():
            return None

        return int(parsed_float)

    except (TypeError, ValueError, OverflowError):
        return None


def parse_year(raw) -> int | None:
    """
    Parse a four-digit year safely.

    Returns None for:
    - missing values
    - blank strings
    - non-numeric values
    - values outside the four-digit year range
    """
    year = parse_int_safe(raw)

    if year is None:
        return None

    if 1000 <= year <= 9999:
        return year

    return None