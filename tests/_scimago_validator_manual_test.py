"""Manual tests for SCImago validation."""

from ingestion.scimago.validator import validate_row


def main() -> None:
    # ---------------------------------------------------------
    # Test 1: fully valid record
    # ---------------------------------------------------------
    valid_record = {
        "sourceid": "23571",
        "title": "Science",
        "sjr": 13.110,
        "sjr_best_quartile": "Q1",
    }

    assert validate_row(valid_record) == []

    # ---------------------------------------------------------
    # Test 2: missing sourceid
    # ---------------------------------------------------------
    record = dict(valid_record)
    record["sourceid"] = ""

    assert validate_row(record) == ["sourceid_missing"]

    # ---------------------------------------------------------
    # Test 3: missing title
    # ---------------------------------------------------------
    record = dict(valid_record)
    record["title"] = "   "

    assert validate_row(record) == ["title_missing"]

    # ---------------------------------------------------------
    # Test 4: None SJR is valid
    # ---------------------------------------------------------
    record = dict(valid_record)
    record["sjr"] = None

    assert validate_row(record) == []

    # ---------------------------------------------------------
    # Test 5: valid SJR boundaries
    # ---------------------------------------------------------
    record = dict(valid_record)
    record["sjr"] = 0

    assert validate_row(record) == []

    record["sjr"] = 100

    assert validate_row(record) == []

    # ---------------------------------------------------------
    # Test 6: SJR below range
    # ---------------------------------------------------------
    record = dict(valid_record)
    record["sjr"] = -0.001

    assert validate_row(record) == ["sjr_out_of_range"]

    # ---------------------------------------------------------
    # Test 7: SJR above range
    # ---------------------------------------------------------
    record = dict(valid_record)
    record["sjr"] = 100.001

    assert validate_row(record) == ["sjr_out_of_range"]

    # ---------------------------------------------------------
    # Test 8: valid quartiles
    # ---------------------------------------------------------
    for quartile in ("Q1", "Q2", "Q3", "Q4"):
        record = dict(valid_record)
        record["sjr_best_quartile"] = quartile

        assert validate_row(record) == []

    # ---------------------------------------------------------
    # Test 9: None quartile is valid
    #
    # Transformer already converts source "-" to None.
    # ---------------------------------------------------------
    record = dict(valid_record)
    record["sjr_best_quartile"] = None

    assert validate_row(record) == []

    # ---------------------------------------------------------
    # Test 10: invalid quartile
    # ---------------------------------------------------------
    record = dict(valid_record)
    record["sjr_best_quartile"] = "Q5"

    assert validate_row(record) == [
        "invalid_sjr_best_quartile"
    ]

    # ---------------------------------------------------------
    # Test 11: multiple validation problems
    # ---------------------------------------------------------
    record = {
        "sourceid": "",
        "title": "",
        "sjr": 101,
        "sjr_best_quartile": "INVALID",
    }

    assert validate_row(record) == [
        "sourceid_missing",
        "title_missing",
        "sjr_out_of_range",
        "invalid_sjr_best_quartile",
    ]

    print("ALL SCIMAGO VALIDATOR MANUAL TESTS PASSED")


if __name__ == "__main__":
    main()