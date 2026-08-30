from __future__ import annotations

from pathlib import Path

from ingestion.abdc.parser import read_abdc_sheet
from ingestion.abdc.transformer import (
    transform_dataframe,
    transform_row,
)


ABDC_PATH = Path(
    "data/raw/abdc/ABDC-JQL-2025-v1-260326.xlsx"
)


EXPECTED_SHEETS = [
    "2025 JQL",
    "2022 JQL",
    "2019 JQL",
    "2016 JQL",
    "2013 JQL",
    "2010 JQL",
]


def test_all_sheets_transform() -> None:
    """
    Verify that every real ABDC worksheet can be transformed into
    normalized JournalHub records.
    """

    for sheet_name in EXPECTED_SHEETS:
        dataframe = read_abdc_sheet(
            ABDC_PATH,
            sheet_name,
        )

        records = transform_dataframe(
            dataframe,
            sheet_name=sheet_name,
        )

        assert len(records) == len(dataframe), (
            f"{sheet_name}: transformed record count does not match "
            "source row count"
        )

        assert records, (
            f"{sheet_name}: no transformed records were produced"
        )

        for record in records:
            assert len(record["source_row_hash"]) == 64
            assert record["rating_year"] in {
                2025,
                2022,
                2019,
                2016,
                2013,
                2010,
            }

            assert record["for_scheme"] in {
                "ANZSRC2008",
                "ANZSRC2020",
            }

            assert record["rating"] in {
                None,
                "A*",
                "A",
                "B",
                "C",
            }


def test_rating_normalization() -> None:
    """
    Verify the real historical ABDC rating behavior:
        lowercase 'c' -> 'C'
        blank rating -> None
    """

    # ---------------------------------------------------------
    # 2010: the real workbook contains one lowercase "c".
    # ---------------------------------------------------------
    dataframe_2010 = read_abdc_sheet(
        ABDC_PATH,
        "2010 JQL",
    )

    lowercase_c_rows = dataframe_2010[
        dataframe_2010["ABDC Ranking"]
        .map(
            lambda value:
                isinstance(value, str)
                and value.strip() == "c"
        )
    ]

    assert len(lowercase_c_rows) == 1, (
        "Expected exactly one lowercase 'c' rating in the "
        "2010 ABDC sheet"
    )

    raw_row = lowercase_c_rows.iloc[0]

    transformed = transform_row(
        raw_row,
        sheet_name="2010 JQL",
    )

    assert transformed["rating"] == "C"


    # ---------------------------------------------------------
    # 2016: the real workbook contains one blank rating.
    # ---------------------------------------------------------
    dataframe_2016 = read_abdc_sheet(
        ABDC_PATH,
        "2016 JQL",
    )

    blank_rating_rows = dataframe_2016[
        dataframe_2016["2016 rating"].isna()
    ]

    assert len(blank_rating_rows) == 1, (
        "Expected exactly one blank rating in the "
        "2016 ABDC sheet"
    )

    raw_row = blank_rating_rows.iloc[0]

    transformed = transform_row(
        raw_row,
        sheet_name="2016 JQL",
    )

    assert transformed["rating"] is None


def test_for_scheme_mapping() -> None:
    """
    Verify the historical FoR scheme mapping required by Day 6.
    """

    expected = {
        "2025 JQL": "ANZSRC2020",
        "2022 JQL": "ANZSRC2008",
        "2019 JQL": "ANZSRC2008",
        "2016 JQL": "ANZSRC2008",
        "2013 JQL": "ANZSRC2008",
        "2010 JQL": "ANZSRC2008",
    }

    for sheet_name, expected_scheme in expected.items():
        dataframe = read_abdc_sheet(
            ABDC_PATH,
            sheet_name,
        )

        records = transform_dataframe(
            dataframe,
            sheet_name=sheet_name,
        )

        assert records, (
            f"{sheet_name}: transformation produced no records"
        )

        assert records[0]["for_scheme"] == expected_scheme, (
            f"{sheet_name}: expected FoR scheme "
            f"{expected_scheme!r}, got "
            f"{records[0]['for_scheme']!r}"
        )

        # Verify every transformed record, not only the first one.
        assert all(
            record["for_scheme"] == expected_scheme
            for record in records
        ), (
            f"{sheet_name}: inconsistent FoR scheme across "
            "transformed records"
        )

def test_historical_column_variants() -> None:
    """
    Verify that historical ABDC column-name differences are correctly
    mapped into the common normalized output fields.
    """

    expected_title_column = {
        "2025 JQL": "Journal Title",
        "2022 JQL": "Journal Title",
        "2019 JQL": "Journal Title",
        "2016 JQL": "Journal Title",
        "2013 JQL": "Journal Name",
        "2010 JQL": "Journal Name",
    }

    expected_for_column = {
        "2025 JQL": "FoR",
        "2022 JQL": "FoR",
        "2019 JQL": "Field of Research",
        "2016 JQL": "Field of Research",
        "2013 JQL": "ABDC FoR code",
        "2010 JQL": "ABDC FoR code",
    }

    for sheet_name in EXPECTED_SHEETS:
        dataframe = read_abdc_sheet(
            ABDC_PATH,
            sheet_name,
        )

        assert expected_title_column[sheet_name] in dataframe.columns
        assert expected_for_column[sheet_name] in dataframe.columns

        records = transform_dataframe(
            dataframe,
            sheet_name=sheet_name,
        )

        first_record = records[0]

        assert "journal_name" in first_record
        assert "for_code" in first_record
        assert "rating" in first_record
        assert "issn" in first_record
        assert "issn_online" in first_record


def test_row_hash_is_stable() -> None:
    """
    The same raw row must always produce the same source-row hash.
    """

    dataframe = read_abdc_sheet(
        ABDC_PATH,
        "2025 JQL",
    )

    raw_row = dataframe.iloc[0]

    first = transform_row(
        raw_row,
        sheet_name="2025 JQL",
    )

    second = transform_row(
        raw_row,
        sheet_name="2025 JQL",
    )

    assert first["source_row_hash"] == second["source_row_hash"]

    assert len(first["source_row_hash"]) == 64


def main() -> None:
    print("ABDC transformer manual verification")
    print("=" * 60)

    print()
    print("Test 1: all six sheets transform")
    test_all_sheets_transform()
    print("PASS")

    print()
    print("Test 2: rating normalization")
    test_rating_normalization()
    print("PASS")

    print()
    print("Test 3: FoR scheme mapping")
    test_for_scheme_mapping()
    print("PASS")

    print()
    print("Test 4: historical column variants")
    test_historical_column_variants()
    print("PASS")

    print()
    print("Test 5: row-hash stability")
    test_row_hash_is_stable()
    print("PASS")

    print()
    print("=" * 60)
    print("ALL ABDC TRANSFORMER ASSERTIONS PASSED")


if __name__ == "__main__":
    main()