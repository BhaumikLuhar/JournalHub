"""Validation for normalized SCImago records."""

from __future__ import annotations

from ingestion.common.validation import is_in_range


_ALLOWED_QUARTILES = {"Q1", "Q2", "Q3", "Q4"}


def validate_row(record: dict) -> list[str]:
    """
    Validate one normalized SCImago record.

    Returns:
        [] when the record is valid.
        A list of validation problem names otherwise.

    Validation rules:
        - sourceid must not be empty.
        - title must not be empty.
        - sjr may be None, otherwise it must be within 0..100.
        - sjr_best_quartile may be None or Q1/Q2/Q3/Q4.

    The record has already passed through the transformer, so a source
    quartile of "-" is already represented as None.
    """
    problems: list[str] = []

    sourceid = record.get("sourceid")
    if sourceid is None or str(sourceid).strip() == "":
        problems.append("sourceid_missing")

    title = record.get("title")
    if title is None or str(title).strip() == "":
        problems.append("title_missing")

    sjr = record.get("sjr")
    if sjr is not None and not is_in_range(sjr, 0, 100):
        problems.append("sjr_out_of_range")

    quartile = record.get("sjr_best_quartile")
    if quartile is not None and quartile not in _ALLOWED_QUARTILES:
        problems.append("invalid_sjr_best_quartile")

    return problems