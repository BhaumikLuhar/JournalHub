from ingestion.common.normalization import (
    normalize_issn,
    split_multi_issn,
    parse_scimago_decimal,
    strip_repec_publisher_suffix,
    normalized_matching_title,
    normalize_scimago_quartile,
    normalize_abdc_rating,
)

import numpy as np
import json

from ingestion.common.csv_loader import compute_row_hash
from ingestion.common.csv_loader import serialize_json_row

# JSON serialization must convert pandas/numpy missing values to JSON null.
json_safe_row = serialize_json_row(
    {
        "Publisher": np.nan,
        "Title": "Science",
        "Rank": np.int64(266),
    }
)

assert json_safe_row["Publisher"] is None
assert json_safe_row["Title"] == "Science"
assert json_safe_row["Rank"] == 266

serialized = json.dumps(
    json_safe_row,
    allow_nan=False,
)

assert '"Publisher": null' in serialized

assert normalize_issn("1614-2411\t") == "16142411"

assert split_multi_issn(
    "10959203, 00368075"
) == ["10959203", "00368075"]

assert parse_scimago_decimal("13,110") == 13.110

assert parse_scimago_decimal("30,18") == 30.18

assert strip_repec_publisher_suffix(
    "Econometrica, Econometric Society"
) == (
    "Econometrica",
    "Econometric Society",
    "high",
)

assert strip_repec_publisher_suffix(
    "Journal of Economics, Theory and Practice"
)[2] == "low"

assert normalized_matching_title(
    "The World Economic Journal"
) == "world economic journal"

assert normalized_matching_title(
    "World Economic Journal"
) == "world economic journal"

assert normalize_scimago_quartile("-") is None

assert normalize_scimago_quartile("Q1") == "Q1"

assert normalize_abdc_rating("c") == "C"

assert normalize_abdc_rating("A* ") == "A*"

assert normalize_abdc_rating(None) is None

assert compute_row_hash(
    {"a": 1, "b": float("nan")}
) == compute_row_hash(
    {"b": None, "a": 1}
)

print("ALL 14 MANUAL TEST ASSERTIONS PASSED")
