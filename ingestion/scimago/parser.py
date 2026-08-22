from __future__ import annotations

import logging
import re
from pathlib import Path

import pandas as pd

from ingestion.common.csv_loader import load_csv


logger = logging.getLogger(__name__)


# Verified against the actual files currently present in:
# data/raw/scimago/
#
# Example:
# scimagojr 2019  Subject Area - Arts and Humanities.csv
#
# The two spaces between the year and "Subject Area" are part of the
# observed filename convention.
_FILENAME_PATTERN = re.compile(
    r"^scimagojr\s+(?P<year>\d{4})\s+Subject Area\s+-\s+(?P<subject_area>.+)\.csv$",
    re.IGNORECASE,
)


def parse_filename(file_name: str) -> dict[str, int | str] | None:
    """
    Parse a verified SCImago filename.

    Expected format:

        scimagojr <year>  Subject Area - <area>.csv

    Example:

        scimagojr 2019  Subject Area - Arts and Humanities.csv

    Returns:
        {
            "year": 2019,
            "subject_area": "Arts and Humanities",
        }

    Returns None when the filename does not match the expected pattern.

    The parser deliberately does not guess or attempt fuzzy recovery from
    malformed filenames.
    """
    name = Path(str(file_name)).name

    match = _FILENAME_PATTERN.fullmatch(name)

    if match is None:
        logger.error(
            "Skipping SCImago file with unexpected filename pattern: %s",
            name,
        )
        return None

    year = int(match.group("year"))
    subject_area = match.group("subject_area").strip()

    if not subject_area:
        logger.error(
            "Skipping SCImago file with empty subject area: %s",
            name,
        )
        return None

    return {
        "year": year,
        "subject_area": subject_area,
    }


def read_scimago_csv(path: str | Path) -> pd.DataFrame:
    """
    Load one SCImago CSV using the shared CSV loader.

    SCImago uses semicolon-separated CSV files.
    Raw values remain under the control of the shared loader; this function
    does not perform normalization.
    """
    return load_csv(path, delimiter=";")