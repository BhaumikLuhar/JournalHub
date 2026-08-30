from pathlib import Path

from ingestion.abdc.parser import (
    FOR_SCHEME_BY_YEAR,
    RATING_COLUMN_BY_SHEET,
    SHEET_YEARS,
    list_supported_sheets,
    read_abdc_sheet,
)


ABDC_PATH = Path(
    "data/raw/abdc/ABDC-JQL-2025-v1-260326.xlsx"
)


def main() -> None:
    print("ABDC parser manual verification")
    print("=" * 60)

    assert ABDC_PATH.exists(), (
        f"ABDC workbook not found: {ABDC_PATH}"
    )

    expected_sheets = [
        "2025 JQL",
        "2022 JQL",
        "2019 JQL",
        "2016 JQL",
        "2013 JQL",
        "2010 JQL",
    ]

    assert list_supported_sheets() == expected_sheets

    assert SHEET_YEARS == {
        "2025 JQL": 2025,
        "2022 JQL": 2022,
        "2019 JQL": 2019,
        "2016 JQL": 2016,
        "2013 JQL": 2013,
        "2010 JQL": 2010,
    }

    assert FOR_SCHEME_BY_YEAR == {
        2025: "ANZSRC2020",
        2022: "ANZSRC2008",
        2019: "ANZSRC2008",
        2016: "ANZSRC2008",
        2013: "ANZSRC2008",
        2010: "ANZSRC2008",
    }

    assert RATING_COLUMN_BY_SHEET == {
        "2025 JQL": "2025 rating",
        "2022 JQL": "2022 rating",
        "2019 JQL": "2019 Rating",
        "2016 JQL": "2016 rating",
        "2013 JQL": "ABDC List 2013",
        "2010 JQL": "ABDC Ranking",
    }

    for sheet_name in expected_sheets:
        dataframe = read_abdc_sheet(
            ABDC_PATH,
            sheet_name,
        )

        print()
        print(f"Sheet: {sheet_name}")
        print(f"Rows: {len(dataframe)}")
        print(f"Columns: {list(dataframe.columns)}")

        assert len(dataframe) > 0

        normalized_columns = {
            str(column).strip().casefold()
            for column in dataframe.columns
        }

        assert (
            "journal name" in normalized_columns
            or "journal title" in normalized_columns
        )

        assert any(
            "issn" in column
            for column in normalized_columns
        )

    print()
    print("=" * 60)
    print("ALL ABDC PARSER ASSERTIONS PASSED")


if __name__ == "__main__":
    main()