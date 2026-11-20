"""Validate the dTEC response and the station-day output contract offline."""

import json
from datetime import UTC, datetime

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest


def _track(root, station, tec, elevation):
    folder = root / "2015" / "120" / station
    folder.mkdir(parents=True, exist_ok=True)
    data = ["# Interval: 30", "# Position (L, B, H): 25, 50, 0", "# Columns: tsn, el, az, tec.l, validity"]
    data.extend(f"{i} {el:.10f} 90 {value:.12f} 0" for i, (value, el) in enumerate(zip(tec, elevation, strict=True)))
    (folder / f"{station}_G05_120_15.dat").write_text("\n".join(data) + "\n", encoding="ascii")


def _run(root, output, *options):
    from tec_variations.__main__ import main

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
                "--tec-parameter",
                "tec.l",
                "--ut-end",
                "0",
                *options,
            ]
        )
        == 0
    )
    return output / "2015" / "120"


@pytest.mark.parametrize(("shortest", "longest", "in_band"), [(2, 10, 5), (10, 20, 14), (20, 60, 35)])
def test_plane_wave_band_response(shortest, longest, in_band):
    from tec_variations.science import bandpass_arc
    from tec_variations.settings import PeriodBand

    minutes = np.arange(1440) / 2
    band = PeriodBand(shortest, longest)
    for period in [1.5, 5, 14, 35, 120]:
        phase = 2 * np.pi * minutes / period
        basis = np.column_stack([np.sin(phase), np.cos(phase)])
        ratios = []
        for distance in [0, 115, 260]:
            shift = 2 * np.pi * distance / 350
            wave = 0.4 * np.sin(phase - shift)
            dtec = bandpass_arc(200 + 0.015 * minutes + 0.00003 * minutes**2 + wave, 30, band)
            interior = np.isfinite(dtec)
            fitted = np.linalg.lstsq(basis[interior], dtec[interior], rcond=None)[0]
            ratios.append(np.linalg.norm(fitted) / 0.4)
            if period == in_band:
                assert fitted / 0.4 == pytest.approx([np.cos(shift), -np.sin(shift)], abs=0.02)
        print(f"{shortest}-{longest} min, {period} min: {min(ratios):.6f}–{max(ratios):.6f}")
        if period == in_band:
            assert ratios == pytest.approx([1, 1, 1], abs=0.1)
        else:
            assert max(ratios) < 0.1


def test_trend_with_phase_offset_is_rejected_before_vertical_projection(tmp_path):
    minutes = np.arange(960) / 2
    _track(tmp_path / "in", "test", 200 + 0.15 * minutes + 0.0003 * minutes**2, 35 + 50 * np.sin(np.pi * minutes / 480))
    new = _run(tmp_path / "in", tmp_path / "new")
    legacy = _run(tmp_path / "in", tmp_path / "legacy", "--filter", "legacy", "--bands", "2-10,10-20,20-60")
    for band in ("dtec_2_10m", "dtec_10_20m", "dtec_20_60m"):
        current = pq.read_table(new / "test/test_dtec_120_15.parquet")[band].drop_null().to_numpy()
        previous = pq.read_table(legacy / "test/test_dtec_120_15.parquet")[band].drop_null().to_numpy()
        assert current.size
        assert np.max(np.abs(current)) < 1e-8
        assert np.max(np.abs(previous)) > 0.02
        print(f"{band} trend maximum (TECU): {np.max(np.abs(current)):.3e} / {np.max(np.abs(previous)):.6f}")


def test_projection_is_samplewise_and_optional(tmp_path):
    minutes = np.arange(400) / 2
    _track(tmp_path / "in", "test", 300 + np.sin(2 * np.pi * minutes / 5), 60 + 25 * np.sin(minutes / 30))
    new = _run(tmp_path / "in", tmp_path / "new", "--bands", "2-10")
    slant = _run(tmp_path / "in", tmp_path / "slant", "--bands", "2-10", "--vertical", "0")
    vertical_table, slant_table = [pq.read_table(day / "test/test_dtec_120_15.parquet") for day in (new, slant)]
    raw = slant_table["dtec_2_10m"].to_numpy()
    projected = vertical_table["dtec_2_10m"].to_numpy()
    assert np.isfinite(raw).any()
    np.testing.assert_allclose(projected, raw * vertical_table["slant_to_vertical_factor"].to_numpy())


def test_elevation_cutoff_splits_arc_even_with_gap_interpolation_enabled(tmp_path):
    minutes = np.arange(240) / 2
    elevation = np.full(len(minutes), 30.0)
    elevation[120] = 29.9
    _track(tmp_path / "in", "test", 20 + np.sin(2 * np.pi * minutes / 5), elevation)
    day = _run(tmp_path / "in", tmp_path / "out", "--bands", "2-10")
    table = pq.read_table(day / "test/test_dtec_120_15.parquet")
    assert table.num_rows == 239
    assert set(table["elevation_deg"].to_pylist()) == {30}
    assert table["arc_id"][119].as_py() != table["arc_id"][120].as_py()
    assert table["dtec_2_10m"][119].as_py() is None
    assert table["dtec_2_10m"][120].as_py() is None
    assert json.loads((day / "diagnostics.json").read_text())["below_elevation_cutoff"] == 1


def test_edge_trimming_and_unavailable_band_are_null_with_diagnostics(tmp_path):
    minutes = np.arange(160) / 2
    _track(tmp_path / "in", "test", np.sin(2 * np.pi * minutes / 5), np.full(len(minutes), 90))
    day = _run(tmp_path / "in", tmp_path / "out")
    table = pq.read_table(day / "test/test_dtec_120_15.parquet")
    assert table.num_rows == 160
    short = table["dtec_2_10m"].to_pylist()
    assert short[:27] == short[-27:] == [None] * 27
    assert short[27] is not None
    assert short[-28] is not None
    assert short[80] is not None
    assert table["dtec_20_60m"].null_count == 160
    assert json.loads((day / "diagnostics.json").read_text())["short_arcs_by_band"]["dtec_20_60m"] == 1


def test_configured_bands_and_parquet_schema_have_no_map_threshold(tmp_path):
    minutes = np.arange(240) / 2
    for station in ("abcd", "test"):
        _track(tmp_path / "in", station, np.sin(2 * np.pi * minutes / 5), np.full(len(minutes), 90))
    day = _run(
        tmp_path / "in",
        tmp_path / "out",
        "--bands",
        "2.5-8,10-25",
        "--di-threshold",
        "100",
        "--map-step-seconds",
        "600",
    )
    for station in ("abcd", "test"):
        table = pq.read_table(day / station / f"{station}_dtec_120_15.parquet")
        assert {field.name: field.type for field in table.schema} == {
            "datetime_utc": pa.timestamp("us", tz="UTC"),
            "station": pa.string(),
            "satellite": pa.string(),
            "elevation_deg": pa.float64(),
            "azimuth_deg": pa.float64(),
            "ipp_latitude_deg": pa.float64(),
            "ipp_longitude_deg": pa.float64(),
            "slant_to_vertical_factor": pa.float64(),
            "arc_id": pa.int32(),
            "dtec_2p5_8m": pa.float64(),
            "dtec_10_25m": pa.float64(),
        }
        assert table.num_rows == 240
        assert table["datetime_utc"][0].as_py() == datetime(2015, 4, 30, tzinfo=UTC)
        assert set(table["station"].to_pylist()) == {station}
        assert set(table["satellite"].to_pylist()) == {"G05"}
        assert 0 < table["dtec_2p5_8m"].null_count < table.num_rows
        metadata = table.schema.metadata
        assert json.loads(metadata[b"bands_minutes"]) == [[2.5, 8], [10, 25]]
        assert metadata[b"filter"] == b"butterworth"
        assert metadata[b"shell_height_km"] == b"300.0"
        assert metadata[b"elevation_cutoff_deg"] == b"30.0"
        assert metadata[b"package_version"]
    assert not list(day.glob("Map_*"))
