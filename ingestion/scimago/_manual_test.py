from pathlib import Path

from ingestion.scimago.parser import (
    parse_filename,
    read_scimago_csv,
)


def main() -> None:
    # ---------------------------------------------------------
    # Filename parsing
    # ---------------------------------------------------------

    parsed = parse_filename(
        "scimagojr 2019  Subject Area - Arts and Humanities.csv"
    )

    assert parsed == {
        "year": 2019,
        "subject_area": "Arts and Humanities",
    }

    parsed = parse_filename(
        "scimagojr 2025  Subject Area - Business, Management and Accounting.csv"
    )

    assert parsed == {
        "year": 2025,
        "subject_area": "Business, Management and Accounting",
    }

    # Malformed filename must be rejected rather than guessed.
    assert (
        parse_filename(
            "scimagojr_2019__Subject_Area_-_Arts_and_Humanities.csv"
        )
        is None
    )

    # ---------------------------------------------------------
    # Actual raw file loading
    # ---------------------------------------------------------

    raw_dir = Path("data/raw/scimago")

    files = sorted(raw_dir.rglob("*.csv"))

    assert files, "No SCImago CSV files found"

    sample_path = files[0]

    parsed = parse_filename(sample_path.name)

    assert parsed is not None
    assert isinstance(parsed["year"], int)
    assert parsed["subject_area"]

    frame = read_scimago_csv(sample_path)

    assert not frame.empty

    required_columns = {
        "Sourceid",
        "Title",
        "Issn",
        "SJR",
        "SJR Best Quartile",
        "Categories",
        "Areas",
    }

    missing = required_columns - set(frame.columns)

    assert not missing, (
        f"Missing expected SCImago columns: {sorted(missing)}"
    )

    print(
        f"PASS: parsed {sample_path.name!r} "
        f"as year={parsed['year']}, "
        f"subject_area={parsed['subject_area']!r}"
    )

    print(
        f"PASS: loaded {len(frame)} rows and "
        f"{len(frame.columns)} columns from the sample file"
    )

    print("ALL SCIMAGO PARSER MANUAL TESTS PASSED")


if __name__ == "__main__":
    main()