"""Tests for rendering TEC map animations in worker processes."""

from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import pytest

from app.tec_map_parallel import load_day_summaries
from app.tec_map_pipeline import DaySummary, TecMapConfig
from app.tec_map_render import TecMapRenderConfig, build_animation_gif_bytes

STATIONS = [("aksu", 55.8, 49.1), ("alme", 54.9, 52.3), ("arsk", 56.1, 49.9), ("kukm", 56.2, 50.9)]


def _frame_summary(frames: int) -> pd.DataFrame:
    rows = []
    for index in range(frames):
        for number, (station, lat, lon) in enumerate(STATIONS):
            rows.append(
                {
                    "frame_time": pd.Timestamp("2026-01-02") + pd.Timedelta(minutes=15 * index),
                    "station": station,
                    "site_lat": lat,
                    "site_lon": lon,
                    "ipp_lat": lat + 0.1,
                    "ipp_lon": lon + 0.1,
                    "vtec_tecu": 10.0 + 5 * number + index,
                    "samples": 4,
                }
            )
    return pd.DataFrame(rows)


def _fake_day(*, year, doy, stations, start_time, end_time, pipeline, root):
    """Stands in for day_frame_summary in a worker; later days finish first."""
    if doy == 99:
        raise FileNotFoundError("no data for day 99")
    time.sleep(0.3 * (4 - doy))
    summary = pd.DataFrame({"frame_time": [pd.Timestamp(start_time)], "station": stations[:1]})
    return DaySummary(year, doy, 10 * doy, 5 * doy, 1, summary, 0.0)


def _days(*doys: int):
    starts = [pd.Timestamp("2026-01-01") + pd.Timedelta(days=doy - 1) for doy in doys]
    return [(2026, doy, start, start + pd.Timedelta(hours=23)) for doy, start in zip(doys, starts, strict=True)]


def test_parallel_drawing_matches_sequential():
    pipeline = TecMapConfig(grid_resolution_deg=1.0, smoothing_sigma=0.0, frame_minutes=15)
    render = TecMapRenderConfig(frame_dpi=50, show_accuracy=True, show_params=True)
    summary = _frame_summary(6)
    stages: list[tuple[str, int, int]] = []

    sequential = build_animation_gif_bytes(frame_summary=summary, pipeline=pipeline, render=render, workers=1)
    parallel = build_animation_gif_bytes(
        frame_summary=summary,
        pipeline=pipeline,
        render=render,
        workers=3,
        progress=lambda stage, done, total: stages.append((stage, done, total)),
    )

    assert parallel[:6] == b"GIF89a"
    assert parallel == sequential
    assert [entry for entry in stages if entry[0] == "drawing"] == [("drawing", n, 6) for n in range(1, 7)]
    assert stages[-1] == ("encoding", 6, 6)


def test_parallel_day_loading_keeps_day_order():
    finished: list[int] = []

    results = load_day_summaries(
        root=Path("/nowhere"),
        stations=["aksu"],
        pipeline=TecMapConfig(),
        days=_days(1, 2, 3),
        workers=3,
        on_day=lambda day: finished.append(day.doy),
        day_fn=_fake_day,
    )

    assert [day.doy for day in results] == [1, 2, 3]
    assert sorted(finished) == [1, 2, 3]
    assert results[1].raw_rows == 20
    assert results[2].summary is not None
    assert results[2].summary["frame_time"].iloc[0] == pd.Timestamp(2026, 1, 3)


def test_parallel_day_loading_raises_a_days_error():
    with pytest.raises(FileNotFoundError, match="day 99"):
        load_day_summaries(
            root=Path("/nowhere"),
            stations=["aksu"],
            pipeline=TecMapConfig(),
            days=_days(1, 99),
            workers=2,
            on_day=lambda day: None,
            day_fn=_fake_day,
        )
