from __future__ import annotations

from datetime import timedelta
import math
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, Response

import pandas as pd

from app import config as cfg
from app.auth import get_current_user_or_401
from app.models import User
from app.tec_map_pipeline import TecMapConfig, build_frame_summary, build_leveled_links, load_tecs_parquet
from app.tec_map_render import TecMapRenderConfig, build_animation_gif_bytes, build_snapshot_plotly_json


router = APIRouter(tags=["tec-map"])


def _scan_root(*paths: str) -> str:
    for path in paths:
        value = (path or "").strip()
        if value:
            return value
    return ""


def _parse_bool(value: bool | str | None) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() not in {"0", "false", "no", "off", ""}


def _normalize_utc_timestamp(value: pd.Timestamp | str) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    return ts


def _resolve_request_day(
    *,
    year: int | None,
    doy: int | None,
    date: str | None,
    label: str,
) -> pd.Timestamp:
    if date:
        return pd.Timestamp(date).normalize()
    if year is None or doy is None:
        raise ValueError(f"Either `{label}_date` or (`{label}_year` and `{label}_doy`) must be provided.")
    return pd.Timestamp(year=int(year), month=1, day=1) + pd.to_timedelta(int(doy) - 1, unit="D")


def _resolve_gif_time_bounds(
    *,
    start_day: pd.Timestamp,
    end_day: pd.Timestamp,
    start_time: str,
    end_time: str,
) -> tuple[pd.Timestamp, pd.Timestamp]:
    start_is_clock = ("T" not in start_time and " " not in start_time)
    end_is_clock = ("T" not in end_time and " " not in end_time)
    if start_is_clock != end_is_clock:
        raise ValueError("Use either HH:MM:SS values for both `start_time` and `end_time`, or full ISO timestamps for both.")

    if start_is_clock:
        start_dt = _normalize_utc_timestamp(f"{start_day.date()} {start_time}")
        end_dt = _normalize_utc_timestamp(f"{end_day.date()} {end_time}")
    else:
        start_dt = _normalize_utc_timestamp(start_time)
        end_dt = _normalize_utc_timestamp(end_time)

    if end_dt < start_dt:
        raise ValueError("GIF range end must be on or after the start.")
    return start_dt, end_dt


def _load_tecs_parquet_gif_range(
    *,
    root: Path,
    start_day: pd.Timestamp,
    end_day: pd.Timestamp,
    start_dt: pd.Timestamp,
    end_dt: pd.Timestamp,
    stations: list[str],
    min_elevation_deg: float,
) -> pd.DataFrame:
    day_frames: list[pd.DataFrame] = []
    current_day = start_day.normalize()
    final_day = end_day.normalize()

    while current_day <= final_day:
        segment_start = max(start_dt, current_day)
        segment_end = min(end_dt, current_day + timedelta(days=1) - timedelta(microseconds=1))
        if segment_end >= segment_start:
            y = int(current_day.year)
            d = int(current_day.timetuple().tm_yday)
            day_frames.append(
                load_tecs_parquet(
                    root=root,
                    year=y,
                    doy=d,
                    stations=stations,
                    start_time=segment_start.isoformat(sep=" "),
                    end_time=segment_end.isoformat(sep=" "),
                    min_elevation_deg=min_elevation_deg,
                )
            )
        current_day = current_day + timedelta(days=1)

    if not day_frames:
        return pd.DataFrame()
    return pd.concat(day_frames, ignore_index=True)


@router.get(
    "/tec-map/gif",
    response_class=Response,
    responses={200: {"content": {"image/gif": {}}}},
)
def tec_map_gif(
    current_user: User = Depends(get_current_user_or_401),
    year: int | None = Query(default=None, ge=2000, le=2100),
    doy: int | None = Query(default=None, ge=1, le=366),
    date: str | None = Query(default=None, description="Optional YYYY-MM-DD; overrides year/doy."),
    end_date: str | None = Query(default=None, description="Optional YYYY-MM-DD end day for multi-day GIF ranges."),
    stations: list[str] = Query(..., min_length=1),
    start_time: str = Query(..., description="ISO timestamp or HH:MM:SS (UTC)."),
    end_time: str = Query(..., description="ISO timestamp or HH:MM:SS (UTC)."),
    min_elevation_deg: float = Query(default=20.0, ge=0.0, le=90.0),
    sampling_interval_seconds: int = Query(default=300, ge=1, le=3600),
    frame_minutes: int = Query(default=15, ge=1, le=240),
    ionosphere_height_km: float = Query(default=350.0, ge=50.0, le=2000.0),
    grid_resolution_deg: float = Query(default=1.0, gt=0.0, le=10.0),
    smoothing_sigma: float = Query(default=1.0, ge=0.0, le=20.0),
    basemap: bool | str | None = Query(default=False, description="Enable OpenStreetMap basemap (requires outbound network)."),
    frame_dpi: int | None = Query(default=None, ge=50, le=200, description="Optional render DPI override (GIF only)."),
):
    # API-style endpoint: keep errors JSON-friendly (no HTML redirects).
    if not (getattr(current_user, "is_admin", False) or (hasattr(current_user, "can_access_page") and current_user.can_access_page("analysis"))):
        raise HTTPException(status_code=403, detail="Forbidden: you do not have access to the Analysis page.")

    data_root = _scan_root(cfg.PARQUET_OUTPUT_TECSUITE_DATA_PATH_CONTAINER, cfg.PARQUET_OUTPUT_TECSUITE_DATA_PATH_HOST)
    if not data_root:
        raise HTTPException(status_code=503, detail="TEC-suite parquet data root is not configured (PARQUET_OUTPUT_TECSUITE_DATA_PATH_*).")

    pipeline = TecMapConfig(
        min_elevation_deg=float(min_elevation_deg),
        sampling_interval_seconds=int(sampling_interval_seconds),
        frame_minutes=int(frame_minutes),
        ionosphere_height_km=float(ionosphere_height_km),
        grid_resolution_deg=float(grid_resolution_deg),
        smoothing_sigma=float(smoothing_sigma),
    )

    basemap_enabled = _parse_bool(basemap)

    # Heuristic: full-day multi-frame animations can get very large at 120dpi.
    # Reduce DPI unless user explicitly overrides.
    chosen_dpi = 120
    if frame_dpi is not None:
        chosen_dpi = int(frame_dpi)
    else:
        try:
            heuristic_start_day = _resolve_request_day(year=year, doy=doy, date=date, label="start")
            heuristic_end_day = pd.Timestamp(end_date).normalize() if end_date else heuristic_start_day
            heuristic_start_dt, heuristic_end_dt = _resolve_gif_time_bounds(
                start_day=heuristic_start_day,
                end_day=heuristic_end_day,
                start_time=start_time,
                end_time=end_time,
            )
            duration_hours = float((heuristic_end_dt - heuristic_start_dt).total_seconds() / 3600.0)
            frame_count = int(math.ceil(duration_hours * 60.0 / max(int(frame_minutes), 1)))
            if frame_count >= 80:
                chosen_dpi = 80
            elif frame_count >= 48:
                chosen_dpi = 90
            elif frame_count >= 24:
                chosen_dpi = 100
        except Exception:
            chosen_dpi = 120

    render = TecMapRenderConfig(
        basemap_enabled=basemap_enabled,
        basemap_cache_root=Path("/app/data/basemap_cache") if basemap_enabled else None,
        frame_dpi=chosen_dpi,
    )

    try:
        range_start_day = _resolve_request_day(year=year, doy=doy, date=date, label="start")
        range_end_day = pd.Timestamp(end_date).normalize() if end_date else range_start_day
        if range_end_day < range_start_day:
            raise ValueError("`end_date` must be on or after the start day.")
        range_start_dt, range_end_dt = _resolve_gif_time_bounds(
            start_day=range_start_day,
            end_day=range_end_day,
            start_time=start_time,
            end_time=end_time,
        )
        raw_links = _load_tecs_parquet_gif_range(
            root=Path(data_root),
            start_day=range_start_dt.normalize(),
            end_day=range_end_dt.normalize(),
            start_dt=range_start_dt,
            end_dt=range_end_dt,
            stations=stations,
            min_elevation_deg=pipeline.min_elevation_deg,
        )
        leveled = build_leveled_links(raw_links, pipeline)
        frame_summary = build_frame_summary(leveled, pipeline)
        gif_bytes = build_animation_gif_bytes(frame_summary=frame_summary, pipeline=pipeline, render=render)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"TEC map GIF rendering failed: {exc}") from exc

    return Response(content=gif_bytes, media_type="image/gif")


@router.get("/tec-map/snapshot")
def tec_map_snapshot(
    current_user: User = Depends(get_current_user_or_401),
    year: int | None = Query(default=None, ge=2000, le=2100),
    doy: int | None = Query(default=None, ge=1, le=366),
    date: str | None = Query(default=None, description="Optional YYYY-MM-DD; overrides year/doy."),
    stations: list[str] = Query(..., min_length=1),
    timestamp: str = Query(..., description="ISO timestamp or HH:MM:SS (UTC)."),
    min_elevation_deg: float = Query(default=20.0, ge=0.0, le=90.0),
    sampling_interval_seconds: int = Query(default=300, ge=1, le=3600),
    frame_minutes: int = Query(default=15, ge=1, le=240),
    ionosphere_height_km: float = Query(default=350.0, ge=50.0, le=2000.0),
    grid_resolution_deg: float = Query(default=1.0, gt=0.0, le=10.0),
    smoothing_sigma: float = Query(default=1.0, ge=0.0, le=20.0),
):
    if not (getattr(current_user, "is_admin", False) or (hasattr(current_user, "can_access_page") and current_user.can_access_page("analysis"))):
        raise HTTPException(status_code=403, detail="Forbidden: you do not have access to the Analysis page.")

    data_root = _scan_root(cfg.PARQUET_OUTPUT_TECSUITE_DATA_PATH_CONTAINER, cfg.PARQUET_OUTPUT_TECSUITE_DATA_PATH_HOST)
    if not data_root:
        raise HTTPException(status_code=503, detail="TEC-suite parquet data root is not configured (PARQUET_OUTPUT_TECSUITE_DATA_PATH_*).")

    pipeline = TecMapConfig(
        min_elevation_deg=float(min_elevation_deg),
        sampling_interval_seconds=int(sampling_interval_seconds),
        frame_minutes=int(frame_minutes),
        ionosphere_height_km=float(ionosphere_height_km),
        grid_resolution_deg=float(grid_resolution_deg),
        smoothing_sigma=float(smoothing_sigma),
    )
    render = TecMapRenderConfig()

    # Snapshot is defined as the `frame_minutes` bin containing the requested timestamp.
    # Load only that bin range (half-open interval [frame_time, frame_time + frame_minutes)).
    try:
        # Derive base day (UTC) from year/doy or date.
        if date:
            day = pd.Timestamp(date).date()
            y = int(day.year)
            d = int(day.timetuple().tm_yday)
        else:
            if year is None or doy is None:
                raise ValueError("Either `date` or (`year` and `doy`) must be provided.")
            y = int(year)
            d = int(doy)

        base_date = pd.Timestamp(year=y, month=1, day=1) + pd.to_timedelta(d - 1, unit="D")

        if "T" in timestamp or " " in timestamp:
            ts = pd.Timestamp(timestamp)
            if ts.tzinfo is not None:
                ts = ts.tz_convert("UTC").tz_localize(None)
        else:
            hh, mm, ss = (int(v) for v in timestamp.split(":"))
            ts = base_date + pd.to_timedelta(hh * 3600 + mm * 60 + ss, unit="s")

        frame_time = ts.floor(f"{pipeline.frame_minutes}min")
        end_dt = frame_time + timedelta(minutes=pipeline.frame_minutes) - timedelta(microseconds=1)
        start_text = frame_time.isoformat(sep=" ")
        end_text = end_dt.isoformat(sep=" ")

        raw_links = load_tecs_parquet(
            root=Path(data_root),
            year=y,
            doy=d,
            stations=stations,
            start_time=start_text,
            end_time=end_text,
            min_elevation_deg=pipeline.min_elevation_deg,
        )
        leveled = build_leveled_links(raw_links, pipeline)
        frame_summary = build_frame_summary(leveled, pipeline)

        payload = build_snapshot_plotly_json(
            frame_summary=frame_summary,
            frame_time=pd.Timestamp(frame_time),
            pipeline=pipeline,
            render=render,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"TEC map snapshot failed: {exc}") from exc

    return JSONResponse(content=payload)
