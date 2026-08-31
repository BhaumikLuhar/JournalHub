from __future__ import annotations

from typing import Any


VALID_ABS_RATINGS = {
    "1",
    "2",
    "3",
    "4",
    "4*",
}


def validate_record(
    record: dict[str, Any],
) -> list[str]:
    """
    Validate one normalized ABS record.

    The allowed rating values are based on the actual ABS source file
    inspected for Day 7, not on an assumed AJG scale.

    Returns:
        A list of validation problems.
        An empty list means the record is valid.
    """

    problems: list[str] = []

    journal_name = record.get("journal_name")

    if not journal_name:
        problems.append(
            "missing journal_name after normalization"
        )

    rating_year = record.get("rating_year")

    if not isinstance(rating_year, int):
        problems.append(
            "rating_year is not an integer"
        )
    elif not 1000 <= rating_year <= 9999:
        problems.append(
            f"rating_year is outside four-digit year range: "
            f"{rating_year!r}"
        )

    rating = record.get("rating")

    if rating not in VALID_ABS_RATINGS:
        problems.append(
            f"invalid ABS rating: {rating!r}; "
            f"expected one of {sorted(VALID_ABS_RATINGS)!r}"
        )

    source_row_hash = record.get("source_row_hash")

    if not source_row_hash:
        problems.append(
            "missing source_row_hash"
        )

    return problems