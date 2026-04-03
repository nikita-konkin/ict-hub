"""
test_data_browser.py — Unit tests for RINEX directory indexing.

These tests cover supported RINEX layouts:
  - YYYY_original/DOY/<station>.zip   where DOY can be 2 or 3 digits
  - YYYY_original/MM/DD/<station>.zip
"""

from pathlib import Path

from app.data_browser import list_rinex_server_structure
from app import data_browser


def _touch_zip(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"zip")


def _year(result: list[dict], name: str) -> dict:
    return next(item for item in result if item["year"] == name)


def setup_function() -> None:
    """Clear module cache so each test runs with a cold scanner cache."""
    data_browser._rinex_cache.clear()


def test_rinex_supports_doy_two_and_three_digit(tmp_path: Path) -> None:
    root = tmp_path / "server"
    _touch_zip(root / "2026_original" / "01" / "aksu0100.zip")
    _touch_zip(root / "2026_original" / "01" / "alex0100.zip")
    _touch_zip(root / "2026_original" / "365" / "aksu3650.zip")

    result = list_rinex_server_structure(str(root))
    year = _year(result, "2026_original")

    assert year["days"] == [
        {"day": "01", "stations": 2},
        {"day": "365", "stations": 1},
    ]


def test_rinex_supports_month_day_layout(tmp_path: Path) -> None:
    root = tmp_path / "server"
    _touch_zip(root / "2025_original" / "03" / "01" / "aksu0030.zip")
    _touch_zip(root / "2025_original" / "03" / "01" / "alex0030.zip")
    _touch_zip(root / "2025_original" / "03" / "02" / "bala0030.zip")

    result = list_rinex_server_structure(str(root))
    year = _year(result, "2025_original")

    assert year["days"] == [
        {"day": "03/01", "stations": 2},
        {"day": "03/02", "stations": 1},
    ]


def test_rinex_month_folder_with_direct_zips_is_counted_as_doy(tmp_path: Path) -> None:
    root = tmp_path / "server"
    _touch_zip(root / "2026_original" / "03" / "aksu0030.zip")
    _touch_zip(root / "2026_original" / "03" / "alex0030.zip")

    result = list_rinex_server_structure(str(root))
    year = _year(result, "2026_original")

    assert year["days"] == [{"day": "03", "stations": 2}]
