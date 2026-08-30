from __future__ import annotations

from pathlib import Path

from ingestion.abdc.parser import read_abdc_sheet
from ingestion.abdc.transformer import transform_dataframe
from ingestion.abdc.validator import (
    is_valid_record,
    validate_record,
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


def test_all_real_abdc_records_are_valid() -> None:
    """
    Every record produced from the real ABDC workbook should pass
    normalized-record validation.
    """

    total_records = 0

    for sheet_name in EXPECTED_SHEETS:
        dataframe = read_abdc_sheet(
            ABDC_PATH,
            sheet_name,
        )

        records = transform_dataframe(
            dataframe,
            sheet_name=sheet_name,
        )

        for record in records:
            errors = validate_record(record)

            assert not errors, (
                f"{sheet_name}: invalid transformed record: "
                f"{record!r}\n"
                f"Errors: {errors}"
            )

            assert is_valid_record(record)

            total_records += 1

    assert total_records > 0


def test_none_rating_is_valid() -> None:
    """
    A missing ABDC rating is valid and must not be rejected.
    """

    record = {
        "source_row_hash": "a" * 64,
        "rating_year": 2016,
        "journal_name": "Example Journal",
        "publisher": "Example Publisher",
        "issn": "1234-5678",
        "issn_online": "8765-4321",
        "year_inception": 2000,
        "for_code": "0101",
        "for_scheme": "ANZSRC2008",
        "rating": None,
    }

    errors = validate_record(record)

    assert errors == []
    assert is_valid_record(record)


def test_valid_ratings() -> None:
    """
    All allowed ABDC ratings must pass.
    """

    for rating in ("A*", "A", "B", "C"):
        record = {
            "source_row_hash": "b" * 64,
            "rating_year": 2025,
            "journal_name": "Example Journal",
            "for_scheme": "ANZSRC2020",
            "rating": rating,
        }

        assert validate_record(record) == []


def test_invalid_rating_is_rejected() -> None:
    """
    An unexpected non-null rating must be rejected.
    """

    record = {
        "source_row_hash": "c" * 64,
        "rating_year": 2025,
        "journal_name": "Example Journal",
        "for_scheme": "ANZSRC2020",
        "rating": "D",
    }

    errors = validate_record(record)

    assert errors
    assert any(
        "rating must be one of" in error
        for error in errors
    )


def test_invalid_rating_year_is_rejected() -> None:
    """
    Only the six supported ABDC rating years are valid.
    """

    record = {
        "source_row_hash": "d" * 64,
        "rating_year": 2024,
        "journal_name": "Example Journal",
        "for_scheme": "ANZSRC2020",
        "rating": "A",
    }

    errors = validate_record(record)

    assert any(
        "rating_year must be one of" in error
        for error in errors
    )


def test_invalid_for_scheme_is_rejected() -> None:
    """
    Unknown FoR schemes must be rejected.
    """

    record = {
        "source_row_hash": "e" * 64,
        "rating_year": 2025,
        "journal_name": "Example Journal",
        "for_scheme": "ANZSRC2015",
        "rating": "A",
    }

    errors = validate_record(record)

    assert any(
        "for_scheme must be one of" in error
        for error in errors
    )


def test_missing_journal_name_is_rejected() -> None:
    """
    Journal name is required for entity resolution.
    """

    record = {
        "source_row_hash": "f" * 64,
        "rating_year": 2025,
        "journal_name": None,
        "for_scheme": "ANZSRC2020",
        "rating": "A",
    }

    errors = validate_record(record)

    assert any(
        "journal_name is required" in error
        for error in errors
    )


def test_invalid_hash_is_rejected() -> None:
    """
    source_row_hash must be a 64-character hexadecimal SHA-256 digest.
    """

    record = {
        "source_row_hash": "not-a-valid-hash",
        "rating_year": 2025,
        "journal_name": "Example Journal",
        "for_scheme": "ANZSRC2020",
        "rating": "A",
    }

    errors = validate_record(record)

    assert any(
        "source_row_hash" in error
        for error in errors
    )


def main() -> None:
    print("ABDC validator manual verification")
    print("=" * 60)

    print()
    print("Test 1: all real ABDC records are valid")
    test_all_real_abdc_records_are_valid()
    print("PASS")

    print()
    print("Test 2: None rating is valid")
    test_none_rating_is_valid()
    print("PASS")

    print()
    print("Test 3: valid ratings")
    test_valid_ratings()
    print("PASS")

    print()
    print("Test 4: invalid rating rejected")
    test_invalid_rating_is_rejected()
    print("PASS")

    print()
    print("Test 5: invalid rating year rejected")
    test_invalid_rating_year_is_rejected()
    print("PASS")

    print()
    print("Test 6: invalid FoR scheme rejected")
    test_invalid_for_scheme_is_rejected()
    print("PASS")

    print()
    print("Test 7: missing journal name rejected")
    test_missing_journal_name_is_rejected()
    print("PASS")

    print()
    print("Test 8: invalid hash rejected")
    test_invalid_hash_is_rejected()
    print("PASS")

    print()
    print("=" * 60)
    print("ALL ABDC VALIDATOR ASSERTIONS PASSED")


if __name__ == "__main__":
    main()