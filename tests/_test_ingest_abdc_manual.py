from __future__ import annotations

from pathlib import Path

from ingestion.abdc.parser import list_supported_sheets


ABDC_PATH = Path(
    "data/raw/abdc/ABDC-JQL-2025-v1-260326.xlsx"
)


def test_pipeline_dependencies() -> None:
    """
    Verify the Part-4 pipeline dependencies before database execution.
    """

    assert ABDC_PATH.exists()
    assert ABDC_PATH.is_file()

    assert list_supported_sheets() == [
        "2025 JQL",
        "2022 JQL",
        "2019 JQL",
        "2016 JQL",
        "2013 JQL",
        "2010 JQL",
    ]


def main() -> None:
    print("ABDC ingestion pipeline preflight")
    print("=" * 60)

    test_pipeline_dependencies()

    print("ABDC workbook exists")
    print("Six supported sheets confirmed")
    print("Parser dependency confirmed")
    print()
    print("=" * 60)
    print("ABDC INGESTION PREFLIGHT PASSED")


if __name__ == "__main__":
    main()