"""Manual tests for SCImago transformation."""

from __future__ import annotations

from pathlib import Path

from ingestion.common.csv_loader import load_csv
from ingestion.scimago.parser import parse_filename
from ingestion.scimago.transformer import (
    parse_areas,
    parse_categories,
    transform_row,
)


BASE_DIR = Path("data/raw/scimago")


# ---------------------------------------------------------------------------
# Test 1: category parsing
# ---------------------------------------------------------------------------

categories = parse_categories(
    "History and Philosophy of Science (Q1); "
    "Multidisciplinary (Q1)"
)

assert categories == [
    {
        "category_name": "History and Philosophy of Science",
        "quartile": "Q1",
    },
    {
        "category_name": "Multidisciplinary",
        "quartile": "Q1",
    },
]


# ---------------------------------------------------------------------------
# Test 2: area parsing
# ---------------------------------------------------------------------------

areas = parse_areas(
    "Arts and Humanities; Multidisciplinary"
)

assert areas == [
    "Arts and Humanities",
    "Multidisciplinary",
]


# ---------------------------------------------------------------------------
# Test 3: transform a real SCImago source row
# ---------------------------------------------------------------------------

sample_path = next(
    BASE_DIR.rglob("scimagojr 2019  Subject Area - *.csv")
)

metadata = parse_filename(sample_path.name)

assert metadata["year"] == 2019
assert metadata["subject_area"] == "Arts and Humanities"

df = load_csv(sample_path, delimiter=";")

assert not df.empty

original_row = df.iloc[0].to_dict()

record = transform_row(
    original_row,
    year=metadata["year"],
    subject_area=metadata["subject_area"],
)


# ---------------------------------------------------------------------------
# Test 4: basic normalized fields
# ---------------------------------------------------------------------------

assert record["sourceid"] == "23571"
assert record["title"] == "Science"


# ---------------------------------------------------------------------------
# Test 5: SCImago decimal comma handling
# ---------------------------------------------------------------------------

assert record["sjr"] == 13.110
assert record["citations_per_doc_2years"] == 11.78
assert record["refs_per_doc"] == 12.90
assert record["female_percentage"] == 30.18


# ---------------------------------------------------------------------------
# Test 6: dynamic year-specific Total Docs column
# ---------------------------------------------------------------------------

assert record["total_docs"] == 3085


# ---------------------------------------------------------------------------
# Test 7: quartile
# ---------------------------------------------------------------------------

assert record["sjr_best_quartile"] == "Q1"


# ---------------------------------------------------------------------------
# Test 8: ISSN normalization
# ---------------------------------------------------------------------------

assert record["issn_list"] == [
    "10959203",
    "00368075",
]


# ---------------------------------------------------------------------------
# Test 9: boolean handling
# ---------------------------------------------------------------------------

assert record["open_access"] is True
assert record["open_access_diamond"] is False


# ---------------------------------------------------------------------------
# Test 10: categories
# ---------------------------------------------------------------------------

assert record["categories"] == [
    {
        "category_name": "History and Philosophy of Science",
        "quartile": "Q1",
    },
    {
        "category_name": "Multidisciplinary",
        "quartile": "Q1",
    },
]


# ---------------------------------------------------------------------------
# Test 11: areas
# ---------------------------------------------------------------------------

assert record["areas"] == [
    "Arts and Humanities",
    "Multidisciplinary",
]


# ---------------------------------------------------------------------------
# Test 12: source-row hash
# ---------------------------------------------------------------------------

assert isinstance(record["source_row_hash"], str)
assert len(record["source_row_hash"]) == 64


# ---------------------------------------------------------------------------
# Test 13: source row was not mutated
# ---------------------------------------------------------------------------

assert original_row["SJR"] == "13,110"
assert original_row["SJR Best Quartile"] == "Q1"
assert original_row["Issn"] == "10959203, 00368075"


# ---------------------------------------------------------------------------
# Test 14: missing quartile becomes None
# ---------------------------------------------------------------------------

test_row = dict(original_row)
test_row["SJR Best Quartile"] = "-"

missing_quartile_record = transform_row(
    test_row,
    year=2019,
    subject_area="Arts and Humanities",
)

assert missing_quartile_record["sjr_best_quartile"] is None


# ---------------------------------------------------------------------------
# Test 15: missing SJR is allowed
# ---------------------------------------------------------------------------

test_row = dict(original_row)
test_row["SJR"] = None

missing_sjr_record = transform_row(
    test_row,
    year=2019,
    subject_area="Arts and Humanities",
)

assert missing_sjr_record["sjr"] is None
assert missing_sjr_record["sjr_out_of_range"] is False


# ---------------------------------------------------------------------------
# Test 16: out-of-range SJR is flagged
# ---------------------------------------------------------------------------

test_row = dict(original_row)
test_row["SJR"] = "101,000"

out_of_range_record = transform_row(
    test_row,
    year=2019,
    subject_area="Arts and Humanities",
)

assert out_of_range_record["sjr"] == 101.0
assert out_of_range_record["sjr_out_of_range"] is True


print("ALL SCIMAGO TRANSFORMER MANUAL TESTS PASSED")