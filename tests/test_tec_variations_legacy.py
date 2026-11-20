"""Offline checks with independently generated tec-suite observations."""

import json
from datetime import date
from math import cos, pi, sin, sqrt

import numpy as np
import pytest


def _fixture(root, day=120, satellite="G01", start=0, stop=80, invalid=None, use_hour=False):
    site = root / "2015" / f"{day:03d}" / "test"
    site.mkdir(parents=True, exist_ok=True)
    path = site / f"test_{satellite}_{day:03d}_15.dat"
    time_column = "hour" if use_hour else "tsn"
    rows = [
        "# Interval: 30.0",
        "# Position (L, B, H): 25, 50, 0",
        f"# Columns: az, datetime, validity, tec.l1l2, {time_column}, el",
    ]
    for index in range(start, stop):
        time = f"{index / 120:.5f}" if use_hour else str(index)
        tec = 10 + 0.04 * index + sin(2 * pi * index / 16)
        rows.append(f"0 2015-04-30 00:00:00 {24 if index == invalid else 0} {tec:.10f} {time} 90")
    path.write_text("\n".join(rows) + "\n", encoding="ascii")
    return path


def test_header_order_datetime_hour_and_missing_phase(tmp_path):
    from tec_variations.dat import read_track

    path = _fixture(tmp_path, invalid=3, stop=6, use_hour=True)
    track = read_track(path, "tec.l1l2")
    assert track.samples[:, 0].tolist() == [0, 30, 60, 120, 150]


def test_filter_matches_independent_symmetric_running_mean():
    from tec_variations.science import filter_arc

    signal = [2 + 0.1 * i + sin(2 * pi * i / 16) for i in range(90)]

    def average(values, index, half_window):
        radius = min(half_window, index, len(values) - index - 1)
        window = values[index - radius : index + radius + 1]
        return sum(window) / len(window)

    smoothed = [average(signal, index, 1) for index in range(len(signal))]
    expected = [value - average(smoothed, index, 19) for index, value in enumerate(smoothed)]
    actual_smooth, actual_di = filter_arc(np.asarray(signal), 30, 2, 20)
    assert actual_smooth == pytest.approx(smoothed, abs=1e-12)
    assert actual_di == pytest.approx(expected, abs=1e-12)


def test_leveling_anchors_vertical_tec_at_the_slant_minimum():
    from tec_variations.science import level_arc

    slant = np.array([9.0, 5.0, 7.0])
    factors = np.array([0.8, 0.5, 0.6])
    assert level_arc(slant, factors, minimum=10, vertical=True) == pytest.approx([19.2, 10, 13.2])
    assert level_arc(slant, factors, minimum=10, vertical=False) == pytest.approx([24, 20, 22])


def test_skipped_arc_edges_do_not_enter_the_filter(tmp_path):
    from tec_variations.dat import read_track
    from tec_variations.pipeline import DayReport, _filter_observations
    from tec_variations.settings import VariationSettings

    track = read_track(_fixture(tmp_path), "tec.l1l2")
    spiked = track.samples.copy()
    spiked[:5, 3] += 1000
    spiked[-5:, 3] -= 1000
    settings = VariationSettings(filter="legacy")
    bands = settings.period_bands
    report = DayReport("2015-04-30")
    original = _filter_observations(track.samples, track, settings, bands, report)
    modified = _filter_observations(spiked, track, settings, bands, report)
    assert np.isfinite(original.variations["dtec_2_20m"]).any()
    np.testing.assert_equal(original.variations["dtec_2_20m"], modified.variations["dtec_2_20m"])


def test_geometry_uses_shell_mapping_and_solar_horizon():
    from tec_variations.science import shell_coordinates, terminator

    lat, lon, factor = shell_coordinates(np.array([30.0]), np.array([90.0]), 0, 0, 300)
    ratio = 6371 / 6671
    assert lat == pytest.approx([0], abs=1e-12)
    assert lon[0] > 0
    assert factor == pytest.approx([sqrt(1 - (ratio * cos(pi / 6)) ** 2)])
    x, y = terminator(date(2015, 3, 20), 43200, 300)
    equatorial = np.abs(y) < 1
    assert np.all((np.abs(x[equatorial]) > 103) & (np.abs(x[equatorial]) < 112))
    assert np.count_nonzero(equatorial) >= 2


def test_cli_map_columns_diagnostics_and_overwrite(tmp_path, capsys):
    from tec_variations.__main__ import main

    root, output = tmp_path / "in", tmp_path / "out"
    _fixture(root, invalid=29)
    _fixture(root, satellite="G02")
    args = [
        "--dat-path",
        str(root),
        "--output-dir",
        str(output),
        "--year",
        "2015",
        "--day-of-year",
        "120",
        "--site",
        "test",
        "--satellite",
        "1",
        "--ut-start",
        "0.25",
        "--ut-end",
        "0.25",
        "--di-threshold",
        "0",
        "--filter",
        "legacy",
        "--legacy-maps",
        "--png",
        "--solar-terminator",
    ]
    assert main(args) == 0
    day = output / "2015" / "120"
    row = (day / "Map_2015_120_UT_ 0.25000.dat").read_text().split()
    assert len(row) == 11
    assert [float(value) for value in row[:3]] == [0.25, 50, 25]
    assert row[9:] == ["test", "test_G01_120_15.dat"]
    assert (day / "Map_2015_120_UT_ 0.25000.png").read_bytes().startswith(b"\x89PNG")
    assert json.loads((day / "diagnostics.json").read_text())["points"] == 1
    assert "previous day" in capsys.readouterr().out
    assert main(args) == 1
    assert "overwrite" in capsys.readouterr().err
    assert main([*args, "--overwrite"]) == 0


def test_neighbor_extends_arc_across_midnight_and_can_be_disabled(tmp_path):
    import pyarrow.parquet as pq

    from tec_variations.__main__ import main

    root, output = tmp_path / "in", tmp_path / "out"
    _fixture(root, day=119, start=2800, stop=2880)
    _fixture(root)
    args = [
        "--dat-path",
        str(root),
        "--output-dir",
        str(output),
        "--year",
        "2015",
        "--days",
        "120",
        "--ut-end",
        "0",
        "--di-threshold",
        "0",
        "--filter",
        "legacy",
        "--legacy-maps",
    ]
    assert main([*args, "--no-previous-day"]) == 0
    day = output / "2015" / "120"
    assert not list(day.glob("Map_*"))
    assert pq.read_table(day / "test/test_dtec_120_15.parquet")["dtec_2_20m"][0].as_py() is None
    assert main([*args, "--overwrite"]) == 0
    assert (output / "2015" / "120" / "Map_2015_120_UT_ 0.00000.dat").stat().st_size > 0


def test_ut_bounds_clip_the_midnight_grid(tmp_path):
    from tec_variations.__main__ import main

    root, output = tmp_path / "in", tmp_path / "out"
    _fixture(root)
    assert (
        main(
            [
                "--dat-path",
                str(root),
                "--output-dir",
                str(output),
                "--year",
                "2015",
                "--day-of-year",
                "120",
                "--ut-start",
                "0.246",
                "--ut-end",
                "0.25",
                "--di-threshold",
                "0",
                "--filter",
                "legacy",
                "--legacy-maps",
            ]
        )
        == 0
    )
    assert [p.name for p in (output / "2015" / "120").glob("*.dat")] == ["Map_2015_120_UT_ 0.25000.dat"]
    assert not list((output / "2015" / "120").glob("*.png"))


def test_renderer_makes_color_limits_effective():
    from tec_variations.output import MapPoint, MapRenderer
    from tec_variations.settings import DEFAULTS

    renderer = MapRenderer(DEFAULTS, None, False)
    assert renderer.dots.get_clim() == (-0.5, 0.5)
    columns = MapPoint(0, 0, 0.1, 10, 0, 90, 0, 0, "test", "test_G01_120_15.dat").format_row(0)
    assert len(columns.rstrip()) == 115
