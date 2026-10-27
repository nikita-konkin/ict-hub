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


def test_list_rinex_station_map_skips_corrupt_archive(tmp_path: Path):
    """A zip whose deflate stream is damaged raises zlib.error; it is skipped, not fatal."""
    _station_map_cache.clear()
    root = tmp_path / "rinex"
    day_root = root / "2026_original" / "001"
    _write_rinex_zip(
        day_root / "aksu0010.zip", "aksu0010.26o", _rinex_header("AKSU", "", (1916390.0, 4329440.0, 4316000.0)) * 40
    )

    broken = day_root / "almx0010.zip"
    _write_rinex_zip(broken, "almx0010.26o", _rinex_header("ALMX", "", (1916000.0, 4329000.0, 4316500.0)) * 40)
    raw = bytearray(broken.read_bytes())
    data_start = raw.index(b"almx0010.26o") + len("almx0010.26o")
    for i in range(data_start + 5, data_start + 40):
        raw[i] ^= 0xFF
    broken.write_bytes(bytes(raw))

    payload = list_rinex_station_map(str(root), year="2026_original", day="001")

    assert [station["station_id"] for station in payload["stations"]] == ["AKSU"]
    assert payload["archive_count"] == 1


def test_ecef_to_geodetic_handles_the_polar_axis():
    import math

    from rinex_station_map import _WGS84_A_M, _WGS84_B_M, _WGS84_E2, _ecef_to_geodetic

    lat, _lon, alt = _ecef_to_geodetic(0.0, 0.0, _WGS84_B_M + 100.0)
    assert abs(lat - 90.0) < 1e-9
    assert abs(alt - 100.0) < 1e-3

    lat, _lon, alt = _ecef_to_geodetic(0.0, 0.0, -(_WGS84_B_M + 2835.0))
    assert abs(lat + 90.0) < 1e-9
    assert abs(alt - 2835.0) < 1e-3

    # Away from the poles: a point on the equator at sea level, and a
    # mid-latitude station (Almaty, ~43.2 N 76.9 E, ~800 m).
    lat, lon, alt = _ecef_to_geodetic(6378137.0, 0.0, 0.0)
    assert abs(lat) < 1e-9
    assert abs(lon) < 1e-9
    assert abs(alt) < 1e-6

    phi, lam, height = math.radians(43.25), math.radians(76.92), 800.0
    n = _WGS84_A_M / math.sqrt(1.0 - _WGS84_E2 * math.sin(phi) ** 2)
    x = (n + height) * math.cos(phi) * math.cos(lam)
    y = (n + height) * math.cos(phi) * math.sin(lam)
    z = (n * (1.0 - _WGS84_E2) + height) * math.sin(phi)
    lat, lon, alt = _ecef_to_geodetic(x, y, z)
    assert abs(lat - 43.25) < 1e-6
    assert abs(lon - 76.92) < 1e-9
    assert abs(alt - 800.0) < 0.01
