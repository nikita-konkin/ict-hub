from __future__ import annotations

import zipfile
from pathlib import Path

from rinex_station_map import _station_map_cache, list_rinex_station_map


def _header_line(value: str, label: str) -> str:
    return f"{value:<60}{label}"


def _rinex_header(marker_name: str, marker_number: str, xyz: tuple[float, float, float]) -> str:
    lines = [
        _header_line("     3.04           OBSERVATION DATA    G", "RINEX VERSION / TYPE"),
        _header_line(marker_name, "MARKER NAME"),
        _header_line(marker_number, "MARKER NUMBER"),
        _header_line(f"{xyz[0]:14.4f}{xyz[1]:14.4f}{xyz[2]:14.4f}", "APPROX POSITION XYZ"),
        _header_line("", "END OF HEADER"),
        "0 0 0 0",
    ]
    return "\n".join(lines)


def _write_rinex_zip(zip_path: Path, member_name: str, content: str) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member_name, content)


def test_list_rinex_station_map_aggregates_duplicate_station_archives(tmp_path: Path):
    _station_map_cache.clear()
    root = tmp_path / "rinex"
    year_root = root / "2026_original" / "001"
    header_a = _rinex_header("AKSU", "12345M001", (1111111.0, 2222222.0, 3333333.0))
    header_b = _rinex_header("AKSU", "12345M001", (1111111.5, 2222222.5, 3333333.5))

    _write_rinex_zip(year_root / "aksu0010.zip", "aksu0010.26o", header_a)
    _write_rinex_zip(year_root / "aksu0011.zip", "aksu0011.26o", header_b)

    payload = list_rinex_station_map(str(root), year="2026_original", day="001")

    assert payload["year"] == "2026_original"
    assert payload["day"] == "001"
    assert payload["archive_count"] == 2
    assert payload["station_count"] == 1

    station = payload["stations"][0]
    assert station["station_id"] == "AKSU"
    assert station["marker_name"] == "AKSU"
    assert station["marker_number"] == "12345M001"
    assert station["archive_count"] == 2
    assert station["days"] == ["001"]
    assert station["coordinate_spread_m"] > 0


def test_list_rinex_station_map_supports_month_day_layout(tmp_path: Path):
    _station_map_cache.clear()
    root = tmp_path / "rinex"
    month_day_root = root / "2026_original" / "01" / "02"
    header = _rinex_header("ARSK", "54321M001", (1444444.0, 2555555.0, 3666666.0))

    _write_rinex_zip(month_day_root / "arsk0020.zip", "arsk0020.26o", header)

    payload = list_rinex_station_map(str(root), year="2026_original", day="01/02")

    assert payload["archive_count"] == 1
    assert payload["station_count"] == 1
    assert payload["stations"][0]["station_id"] == "ARSK"
    assert payload["stations"][0]["days"] == ["01/02"]
