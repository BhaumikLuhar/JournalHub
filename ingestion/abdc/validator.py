from __future__ import annotations

from typing import Any


VALID_RATING_YEARS = {
    2010,
    2013,
    2016,
    2019,
    2022,
    2025,
}

VALID_FOR_SCHEMES = {
    "ANZSRC2008",
    "ANZSRC2020",
}

VALID_RATINGS = {
    "A*",
    "A",
    "B",
    "C",
}


REQUIRED_FIELDS = (
    "source_row_hash",
    "rating_year",
    "journal_name",
    "for_scheme",
)


def validate_record(
    record: dict[str, Any],
) -> list[str]:
    """
    Validate one normalized ABDC record.

    The record is expected to have already passed through transformer.py.

    Returns:
        A list of validation error messages.

        An empty list means the record is valid.

    Important:
        rating=None is valid and represents a missing source rating.
    """

    errors: list[str] = []

    # ---------------------------------------------------------
    # Required fields
    # ---------------------------------------------------------

    for field_name in REQUIRED_FIELDS:
        value = record.get(field_name)

        if value is None:
            errors.append(
                f"{field_name} is required"
            )
            continue

        if isinstance(value, str) and not value.strip():
            errors.append(
                f"{field_name} is required"
            )

    # ---------------------------------------------------------
    # Source-row hash
    # ---------------------------------------------------------

    source_row_hash = record.get("source_row_hash")

    if source_row_hash is not None:
        if not isinstance(source_row_hash, str):
            errors.append(
                "source_row_hash must be a string"
            )
        elif len(source_row_hash) != 64:
            errors.append(
                "source_row_hash must be a 64-character SHA-256 "
                "hexadecimal digest"
            )
        else:
            try:
                int(source_row_hash, 16)
            except ValueError:
                errors.append(
                    "source_row_hash must contain only hexadecimal "
                    "characters"
                )

    # ---------------------------------------------------------
    # Rating year
    # ---------------------------------------------------------

    rating_year = record.get("rating_year")

    if rating_year is not None:
        if rating_year not in VALID_RATING_YEARS:
            errors.append(
                f"rating_year must be one of "
                f"{sorted(VALID_RATING_YEARS)}; "
                f"got {rating_year!r}"
            )

    # ---------------------------------------------------------
    # FoR scheme
    # ---------------------------------------------------------

    for_scheme = record.get("for_scheme")

    if for_scheme is not None:
        if for_scheme not in VALID_FOR_SCHEMES:
            errors.append(
                f"for_scheme must be one of "
                f"{sorted(VALID_FOR_SCHEMES)}; "
                f"got {for_scheme!r}"
            )

    # ---------------------------------------------------------
    # Rating
    # ---------------------------------------------------------
    #
    # None is intentionally VALID.
    #
    # The source contains one blank 2016 rating, and the normalized
    # representation for that source value is None.
    # ---------------------------------------------------------

    rating = record.get("rating")

    if rating is not None:
        if rating not in VALID_RATINGS:
            errors.append(
                f"rating must be one of "
                f"{sorted(VALID_RATINGS)} or None; "
                f"got {rating!r}"
            )

    return errors


def is_valid_record(
    record: dict[str, Any],
) -> bool:
    """
    Return True when a normalized ABDC record passes validation.
    """

    return not validate_record(record)