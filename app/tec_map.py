from __future__ import annotations

from datetime import timedelta
import logging
import math
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, Response

import pandas as pd

from dataclasses import replace

from app import config as cfg
from app.auth import get_current_user_or_401
from app.models import User
from app.tec_map_fields import resolve_signal_band
from app.tec_map_pipeline import TecMapConfig, build_frame_summary, build_leveled_links, load_tecs_parquet
from app.tec_map_validation import loso_cross_validate, summarize_validation
from app.tec_map_render import (
    ANIMATION_MEDIA_TYPES,
    TecMapRenderConfig,
    build_animation_gif_bytes,
    build_frame_image_bytes,
    build_snapshot_plotly_json,
)


logger = logging.getLogger(__name__)

router = APIRouter(tags=["tec-map"])

# Guard rails for the GIF endpoint: a request larger than this is rejected with
# 413 instead of being allowed to exhaust container memory / render for hours.
TEC_MAP_MAX_STATIONS_PER_REQUEST = 40
TEC_MAP_MAX_FRAMES_PER_REQUEST = 800


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


def _parse_normalize_stations(value: str | None) -> str:
    text = str(value or "off").strip().lower()
    if text in {"", "off", "false", "no", "0"}:
        return "off"
    if text in {"auto", "missing", "fallback", "nan"}:
        return "auto"
    if text in {"always", "all", "true", "1", "yes", "on"}:
        return "always"
    raise ValueError("Unsupported normalize_stations. Use one of: off, auto, always.")


def _parse_field(value: str | None) -> str:
    text = str(value or "vtec").strip().lower()
    if text in {"", "vtec", "v", "magnitude"}:
        return "vtec"
    if text in {"vtec_gradient", "gradient", "grad", "|grad|", "vtec_grad"}:
        return "vtec_gradient"
    if text in {"gdd", "d", "dispersion", "group_delay_dispersion"}:
        return "gdd"
    if text in {"b_k", "bk", "cb", "coherence_bandwidth"}:
        return "b_k"
    raise ValueError("Unsupported field. Use one of: vtec, vtec_gradient, gdd, b_k.")


def _parse_animation_format(value: str | None) -> str:
    text = str(value or "gif").strip().lower()
    if text in {"", "gif"}:
        return "gif"
    if text in {"mp4", "h264", "video"}:
        return "mp4"
    if text in {"webm", "vp9"}:
        return "webm"
    raise ValueError("Unsupported format. Use one of: gif, mp4, webm.")


def _parse_render_quality(value: str | None) -> str:
    text = str(value or "standard").strip().lower()
    if text in {"", "standard", "fast", "default"}:
        return "standard"
    if text in {"high", "hq", "publication"}:
        return "high"
    raise ValueError("Unsupported quality. Use one of: standard, high.")


def _parse_interpolation(value: str | None) -> str:
    text = str(value or "linear").strip().lower()
    if text in {"", "linear", "delaunay", "griddata"}:
        return "linear"
    if text in {"kriging", "krige", "ok", "ordinary_kriging"}:
        return "kriging"
    raise ValueError("Unsupported interpolation. Use one of: linear, kriging.")


def _parse_validation_interpolations(value: str | None) -> list[str]:
    text = str(value or "both").strip().lower()
    if text in {"", "both", "all", "compare"}:
        return ["linear", "kriging"]
    return [_parse_interpolation(text)]


def _parse_basemap_mode(value: bool | str | None) -> str:
    if isinstance(value, bool):
        return "openstreetmap" if value else "off"

    text = str(value or "").strip().lower()
    if text in {"", "0", "false", "no", "off"}:
        return "off"
    if text in {"1", "true", "yes", "on", "osm", "openstreetmap"}:
        return "openstreetmap"
    if text in {"cache", "cache_only", "offline_cache"}:
        return "cache_only"
    if text in {"tile_server", "tileserver", "server", "http", "xyz"}:
        return "tile_server"

    raise ValueError(
        "Unsupported basemap mode. Use one of: off, cache_only, tile_server, openstreetmap."
    )


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


def _station_dir_exists(root: Path, year: int, doy: int, station: str) -> bool:
    day_dir = Path(root) / str(year) / f"{doy:03d}"
    station = station.lower()
    return (day_dir / station).is_dir() or (day_dir / f"{station}{doy:03d}0").is_dir()


def _validate_stations_for_range(
    *,
    root: Path,
    start_day: pd.Timestamp,
    end_day: pd.Timestamp,
    stations: list[str],
) -> list[str]:
    """
    Return the requested stations that exist in at least one day of the range.
    Raises ValueError naming stations that exist in none of the days (typos).
    """
    requested = [str(s).strip().lower() for s in stations if str(s).strip()]
    days: list[tuple[int, int]] = []
    current_day = start_day.normalize()
    while current_day <= end_day.normalize():
        days.append((int(current_day.year), int(current_day.timetuple().tm_yday)))
        current_day = current_day + timedelta(days=1)

    found = [s for s in requested if any(_station_dir_exists(root, y, d, s) for (y, d) in days)]
    unknown = [s for s in requested if s not in found]
    if unknown:
        raise ValueError(
            f"Unknown stations (no data for any requested day): {', '.join(sorted(unknown))}. "
            "Check the spelling against the parquet dataset."
        )
    return found


def _build_frame_summary_gif_range(
    *,
    root: Path,
    start_day: pd.Timestamp,
    end_day: pd.Timestamp,
    start_dt: pd.Timestamp,
    end_dt: pd.Timestamp,
    stations: list[str],
    pipeline: TecMapConfig,
) -> pd.DataFrame:
    """
    Stream the range day by day: load → level → frame summary, keeping only the
    small per-frame summaries. Peak memory stays bounded by one day regardless
    of the range length. Arc leveling and MSTD estimation group by date anyway,
    so the result is identical to processing the whole range at once.
    """
    day_summaries: list[pd.DataFrame] = []
    current_day = start_day.normalize()
    final_day = end_day.normalize()

    while current_day <= final_day:
        segment_start = max(start_dt, current_day)
        segment_end = min(end_dt, current_day + timedelta(days=1) - timedelta(microseconds=1))
        if segment_end >= segment_start:
            y = int(current_day.year)
            d = int(current_day.timetuple().tm_yday)
            day_started = time.monotonic()
            raw_links = load_tecs_parquet(
                root=root,
                year=y,
                doy=d,
                stations=stations,
                start_time=segment_start.isoformat(sep=" "),
                end_time=segment_end.isoformat(sep=" "),
                min_elevation_deg=pipeline.min_elevation_deg,
            )
            if raw_links.empty:
                logger.info("tec-map gif: %04d-%03d — no samples for requested stations, day skipped", y, d)
            else:
                leveled = build_leveled_links(raw_links, pipeline)
                summary = build_frame_summary(leveled, pipeline)
                logger.info(
                    "tec-map gif: %04d-%03d — raw=%d leveled=%d frames=%d stations=%d (%.1fs)",
                    y,
                    d,
                    len(raw_links),
                    len(leveled),
                    summary["frame_time"].nunique() if not summary.empty else 0,
                    raw_links["station"].nunique(),
                    time.monotonic() - day_started,
                )
                if not summary.empty:
                    day_summaries.append(summary)
        current_day = current_day + timedelta(days=1)

    if not day_summaries:
        return pd.DataFrame()
    return pd.concat(day_summaries, ignore_index=True)


def _load_single_frame_summary(
    *,
    data_root: str,
    year: int | None,
    doy: int | None,
    date: str | None,
    timestamp: str,
    stations: list[str],
    pipeline: TecMapConfig,
    field_mode: str,
    log_label: str,
) -> tuple[pd.Timestamp, pd.DataFrame]:
    """
    Load the frame-summary for the single `frame_minutes` bin containing
    `timestamp` (shared by the snapshot and frame endpoints).
    """
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

    found_stations = _validate_stations_for_range(
        root=Path(data_root),
        start_day=base_date,
        end_day=base_date,
        stations=stations,
    )
    logger.info(
        "tec-map %s: %s stations=%s field=%s frame=%s",
        log_label,
        base_date.date(),
        ",".join(found_stations),
        field_mode,
        frame_time,
    )
    raw_links = load_tecs_parquet(
        root=Path(data_root),
        year=y,
        doy=d,
        stations=found_stations,
        start_time=frame_time.isoformat(sep=" "),
        end_time=end_dt.isoformat(sep=" "),
        min_elevation_deg=pipeline.min_elevation_deg,
    )
    leveled = build_leveled_links(raw_links, pipeline)
    frame_summary = build_frame_summary(leveled, pipeline)
    return pd.Timestamp(frame_time), frame_summary


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
    interpolation: str = Query(
        default="linear",
        description="Grid interpolation: linear (Delaunay + nearest fill) or kriging (ordinary kriging, exponential variogram fitted per frame).",
    ),
    vtec_smooth_epochs: int = Query(
        default=0,
        ge=0,
        le=50,
        description="Rolling-median window in epochs per (station, satellite, arc). 0 disables.",
    ),
    normalize_stations: str = Query(
        default="off",
        description="Per-station VTEC median-shift: off (default), auto (only when MSTD bias failed), always.",
    ),
    basemap: bool | str | None = Query(
        default=False,
        description="Basemap mode: off, cache_only, tile_server, or openstreetmap. Legacy true/false also accepted.",
    ),
    field: str = Query(
        default="vtec",
        description=(
            "Scalar field to render: vtec (default), vtec_gradient (|∇VTEC| in TECU/100km), "
            "gdd (group delay dispersion |D| in ns/GHz), b_k (coherence bandwidth in MHz)."
        ),
    ),
    signal_band: str = Query(
        default="gps_l1",
        description="Carrier band for gdd/b_k fields (e.g. gps_l1, gps_l5, galileo_e1, bds_b2a).",
    ),
    frame_dpi: int | None = Query(default=None, ge=50, le=300, description="Optional render DPI override."),
    format: str = Query(
        default="gif",
        description="Animation container: gif (default), mp4 (H.264) or webm (VP9). Video formats avoid the 256-colour GIF palette.",
    ),
    quality: str = Query(
        default="standard",
        description=(
            "standard (fast) or high. High: adaptive GIF palette with Floyd–Steinberg dithering "
            "and no automatic DPI reduction for long ranges."
        ),
    ),
    upsample: int = Query(
        default=2,
        ge=1,
        le=4,
        description="Render-grid upsampling factor: field is bilinearly upsampled and the coverage mask is evaluated at render resolution (round edges). 1 = off.",
    ),
    color_min: float | None = Query(default=None, description="Explicit lower colour-scale limit (field units)."),
    color_max: float | None = Query(default=None, description="Explicit upper colour-scale limit (field units)."),
    basemap_alpha: float = Query(default=0.28, ge=0.0, le=1.0, description="Basemap tile layer opacity (0 = invisible, 1 = opaque)."),
    field_alpha: float | None = Query(default=None, ge=0.0, le=1.0, description="Field layer opacity; default 0.72 over a basemap, 0.95 without."),
    show_accuracy: bool = Query(
        default=False,
        description="Annotate each frame with its leave-one-station-out accuracy (LOSO RMSE in TECU).",
    ),
):
    # API-style endpoint: keep errors JSON-friendly (no HTML redirects).
    if not (getattr(current_user, "is_admin", False) or (hasattr(current_user, "can_access_page") and current_user.can_access_page("analysis"))):
        raise HTTPException(status_code=403, detail="Forbidden: you do not have access to the Analysis page.")

    data_root = _scan_root(cfg.PARQUET_OUTPUT_TECSUITE_DATA_PATH_CONTAINER, cfg.PARQUET_OUTPUT_TECSUITE_DATA_PATH_HOST)
    if not data_root:
        raise HTTPException(status_code=503, detail="TEC-suite parquet data root is not configured (PARQUET_OUTPUT_TECSUITE_DATA_PATH_*).")

    try:
        normalize_mode = _parse_normalize_stations(normalize_stations)
        interp_mode = _parse_interpolation(interpolation)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    pipeline = TecMapConfig(
        min_elevation_deg=float(min_elevation_deg),
        sampling_interval_seconds=int(sampling_interval_seconds),
        frame_minutes=int(frame_minutes),
        ionosphere_height_km=float(ionosphere_height_km),
        grid_resolution_deg=float(grid_resolution_deg),
        smoothing_sigma=float(smoothing_sigma),
        interpolation_method=interp_mode,
        vtec_smooth_epochs=int(vtec_smooth_epochs),
        normalize_stations=normalize_mode,
    )

    basemap_mode = _parse_basemap_mode(basemap)
    basemap_enabled = basemap_mode != "off"
    basemap_cache_root = cfg.TEC_MAP_BASEMAP_CACHE_ROOT.strip() if basemap_enabled else ""
    basemap_tile_server_url = cfg.TEC_MAP_BASEMAP_TILE_SERVER_URL.strip() if basemap_enabled else ""

    try:
        animation_format = _parse_animation_format(format)
        render_quality = _parse_render_quality(quality)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Heuristic: full-day multi-frame GIFs can get very large at 120dpi.
    # Reduce DPI unless the user overrides it or asked for high quality;
    # video formats compress far better, so they keep full DPI too.
    chosen_dpi = 120
    if frame_dpi is not None:
        chosen_dpi = int(frame_dpi)
    elif animation_format != "gif" or render_quality == "high":
        chosen_dpi = 120
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

    try:
        field_mode = _parse_field(field)
        signal_band_mode, _ = resolve_signal_band(signal_band)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    render = TecMapRenderConfig(
        field=field_mode,
        signal_band=signal_band_mode,
        basemap_enabled=basemap_enabled,
        basemap_mode=basemap_mode,
        basemap_cache_root=Path(basemap_cache_root) if basemap_cache_root else None,
        basemap_tile_server_url=basemap_tile_server_url or None,
        basemap_fallback_to_plain=cfg.TEC_MAP_BASEMAP_FALLBACK_TO_PLAIN,
        frame_dpi=chosen_dpi,
        basemap_alpha=float(basemap_alpha),
        vtec_layer_alpha=field_alpha,
        animation_format=animation_format,
        gif_high_quality=(render_quality == "high"),
        upsample_factor=int(upsample),
        color_min=color_min,
        color_max=color_max,
        show_accuracy=bool(show_accuracy),
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
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Guard rails before any data is loaded: reject oversized requests with a
    # clear message instead of exhausting container memory or rendering forever.
    if len(stations) > TEC_MAP_MAX_STATIONS_PER_REQUEST:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Too many stations: {len(stations)} > {TEC_MAP_MAX_STATIONS_PER_REQUEST}. "
                "Reduce the station list or split the request."
            ),
        )
    duration_minutes = (range_end_dt - range_start_dt).total_seconds() / 60.0
    estimated_frames = int(math.ceil(duration_minutes / max(int(frame_minutes), 1)))
    if estimated_frames > TEC_MAP_MAX_FRAMES_PER_REQUEST:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Requested range would render ~{estimated_frames} frames "
                f"(limit {TEC_MAP_MAX_FRAMES_PER_REQUEST}). Shorten the date range or "
                "increase frame_minutes."
            ),
        )

    request_started = time.monotonic()
    logger.info(
        "tec-map gif: %s..%s stations=%s field=%s format=%s quality=%s frames~%d grid=%.2fdeg dpi=%d upsample=%d basemap=%s",
        range_start_dt,
        range_end_dt,
        ",".join(stations),
        field_mode,
        animation_format,
        render_quality,
        estimated_frames,
        pipeline.grid_resolution_deg,
        chosen_dpi,
        int(upsample),
        basemap_mode,
    )

    try:
        found_stations = _validate_stations_for_range(
            root=Path(data_root),
            start_day=range_start_dt.normalize(),
            end_day=range_end_dt.normalize(),
            stations=stations,
        )
        frame_summary = _build_frame_summary_gif_range(
            root=Path(data_root),
            start_day=range_start_dt.normalize(),
            end_day=range_end_dt.normalize(),
            start_dt=range_start_dt,
            end_dt=range_end_dt,
            stations=found_stations,
            pipeline=pipeline,
        )
        if frame_summary.empty:
            raise FileNotFoundError("No samples found for the requested stations/time range.")
        logger.info(
            "tec-map gif: rendering %d frames at %d dpi (%s)…",
            frame_summary["frame_time"].nunique(),
            chosen_dpi,
            animation_format,
        )
        animation_bytes = build_animation_gif_bytes(frame_summary=frame_summary, pipeline=pipeline, render=render)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("tec-map gif: rendering failed")
        raise HTTPException(status_code=500, detail=f"TEC map animation rendering failed: {exc}") from exc

    logger.info(
        "tec-map gif: done — %.1f MB (%s) in %.1fs",
        len(animation_bytes) / 1e6,
        animation_format,
        time.monotonic() - request_started,
    )
    return Response(
        content=animation_bytes,
        media_type=ANIMATION_MEDIA_TYPES[animation_format],
        headers={"Content-Disposition": f'inline; filename="tec_map.{animation_format}"'},
    )


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
    interpolation: str = Query(
        default="linear",
        description="Grid interpolation: linear (Delaunay + nearest fill) or kriging (ordinary kriging, exponential variogram fitted per frame).",
    ),
    vtec_smooth_epochs: int = Query(
        default=0,
        ge=0,
        le=50,
        description="Rolling-median window in epochs per (station, satellite, arc). 0 disables.",
    ),
    normalize_stations: str = Query(
        default="off",
        description="Per-station VTEC median-shift: off (default), auto (only when MSTD bias failed), always.",
    ),
    field: str = Query(
        default="vtec",
        description=(
            "Scalar field to render: vtec (default), vtec_gradient (|∇VTEC| in TECU/100km), "
            "gdd (group delay dispersion |D| in ns/GHz), b_k (coherence bandwidth in MHz)."
        ),
    ),
    signal_band: str = Query(
        default="gps_l1",
        description="Carrier band for gdd/b_k fields (e.g. gps_l1, gps_l5, galileo_e1, bds_b2a).",
    ),
    show_accuracy: bool = Query(
        default=False,
        description="Annotate the snapshot with its leave-one-station-out accuracy (LOSO RMSE in TECU).",
    ),
):
    if not (getattr(current_user, "is_admin", False) or (hasattr(current_user, "can_access_page") and current_user.can_access_page("analysis"))):
        raise HTTPException(status_code=403, detail="Forbidden: you do not have access to the Analysis page.")

    data_root = _scan_root(cfg.PARQUET_OUTPUT_TECSUITE_DATA_PATH_CONTAINER, cfg.PARQUET_OUTPUT_TECSUITE_DATA_PATH_HOST)
    if not data_root:
        raise HTTPException(status_code=503, detail="TEC-suite parquet data root is not configured (PARQUET_OUTPUT_TECSUITE_DATA_PATH_*).")

    try:
        normalize_mode = _parse_normalize_stations(normalize_stations)
        interp_mode = _parse_interpolation(interpolation)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    pipeline = TecMapConfig(
        min_elevation_deg=float(min_elevation_deg),
        sampling_interval_seconds=int(sampling_interval_seconds),
        frame_minutes=int(frame_minutes),
        ionosphere_height_km=float(ionosphere_height_km),
        grid_resolution_deg=float(grid_resolution_deg),
        smoothing_sigma=float(smoothing_sigma),
        interpolation_method=interp_mode,
        vtec_smooth_epochs=int(vtec_smooth_epochs),
        normalize_stations=normalize_mode,
    )
    try:
        field_mode = _parse_field(field)
        signal_band_mode, _ = resolve_signal_band(signal_band)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    render = TecMapRenderConfig(field=field_mode, signal_band=signal_band_mode, show_accuracy=bool(show_accuracy))

    # Snapshot is defined as the `frame_minutes` bin containing the requested timestamp.
    # Load only that bin range (half-open interval [frame_time, frame_time + frame_minutes)).
    try:
        frame_time, frame_summary = _load_single_frame_summary(
            data_root=data_root,
            year=year,
            doy=doy,
            date=date,
            timestamp=timestamp,
            stations=stations,
            pipeline=pipeline,
            field_mode=field_mode,
            log_label="snapshot",
        )

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


FRAME_IMAGE_MEDIA_TYPES = {
    "png": "image/png",
    "svg": "image/svg+xml",
}


@router.get(
    "/tec-map/frame",
    response_class=Response,
    responses={200: {"content": {"image/png": {}, "image/svg+xml": {}}}},
)
def tec_map_frame(
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
    interpolation: str = Query(
        default="linear",
        description="Grid interpolation: linear (Delaunay + nearest fill) or kriging (ordinary kriging, exponential variogram fitted per frame).",
    ),
    vtec_smooth_epochs: int = Query(
        default=0,
        ge=0,
        le=50,
        description="Rolling-median window in epochs per (station, satellite, arc). 0 disables.",
    ),
    normalize_stations: str = Query(
        default="off",
        description="Per-station VTEC median-shift: off (default), auto (only when MSTD bias failed), always.",
    ),
    basemap: bool | str | None = Query(
        default=False,
        description="Basemap mode: off, cache_only, tile_server, or openstreetmap. Legacy true/false also accepted.",
    ),
    field: str = Query(
        default="vtec",
        description=(
            "Scalar field to render: vtec (default), vtec_gradient (|∇VTEC| in TECU/100km), "
            "gdd (group delay dispersion |D| in ns/GHz), b_k (coherence bandwidth in MHz)."
        ),
    ),
    signal_band: str = Query(
        default="gps_l1",
        description="Carrier band for gdd/b_k fields (e.g. gps_l1, gps_l5, galileo_e1, bds_b2a).",
    ),
    dpi: int = Query(default=200, ge=50, le=600, description="Render DPI (publication figures: 300–600)."),
    image_format: str = Query(default="png", description="Output image format: png or svg."),
    upsample: int = Query(
        default=2,
        ge=1,
        le=4,
        description="Render-grid upsampling factor: field is bilinearly upsampled and the coverage mask is evaluated at render resolution (round edges). 1 = off.",
    ),
    color_min: float | None = Query(default=None, description="Explicit lower colour-scale limit (field units)."),
    color_max: float | None = Query(default=None, description="Explicit upper colour-scale limit (field units)."),
    basemap_alpha: float = Query(default=0.28, ge=0.0, le=1.0, description="Basemap tile layer opacity (0 = invisible, 1 = opaque)."),
    field_alpha: float | None = Query(default=None, ge=0.0, le=1.0, description="Field layer opacity; default 0.72 over a basemap, 0.95 without."),
    show_accuracy: bool = Query(
        default=False,
        description="Annotate the frame with its leave-one-station-out accuracy (LOSO RMSE in TECU).",
    ),
):
    """
    Publication-quality static frame (PNG/SVG) — same visual pipeline as one
    animation frame, but at up to 600 dpi. Intended for article figures.
    """
    if not (getattr(current_user, "is_admin", False) or (hasattr(current_user, "can_access_page") and current_user.can_access_page("analysis"))):
        raise HTTPException(status_code=403, detail="Forbidden: you do not have access to the Analysis page.")

    data_root = _scan_root(cfg.PARQUET_OUTPUT_TECSUITE_DATA_PATH_CONTAINER, cfg.PARQUET_OUTPUT_TECSUITE_DATA_PATH_HOST)
    if not data_root:
        raise HTTPException(status_code=503, detail="TEC-suite parquet data root is not configured (PARQUET_OUTPUT_TECSUITE_DATA_PATH_*).")

    try:
        normalize_mode = _parse_normalize_stations(normalize_stations)
        interp_mode = _parse_interpolation(interpolation)
        field_mode = _parse_field(field)
        signal_band_mode, _ = resolve_signal_band(signal_band)
        basemap_mode = _parse_basemap_mode(basemap)
        output_format = str(image_format or "png").strip().lower()
        if output_format not in FRAME_IMAGE_MEDIA_TYPES:
            raise ValueError("Unsupported image_format. Use one of: png, svg.")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    pipeline = TecMapConfig(
        min_elevation_deg=float(min_elevation_deg),
        sampling_interval_seconds=int(sampling_interval_seconds),
        frame_minutes=int(frame_minutes),
        ionosphere_height_km=float(ionosphere_height_km),
        grid_resolution_deg=float(grid_resolution_deg),
        smoothing_sigma=float(smoothing_sigma),
        interpolation_method=interp_mode,
        vtec_smooth_epochs=int(vtec_smooth_epochs),
        normalize_stations=normalize_mode,
    )

    basemap_enabled = basemap_mode != "off"
    basemap_cache_root = cfg.TEC_MAP_BASEMAP_CACHE_ROOT.strip() if basemap_enabled else ""
    basemap_tile_server_url = cfg.TEC_MAP_BASEMAP_TILE_SERVER_URL.strip() if basemap_enabled else ""

    render = TecMapRenderConfig(
        field=field_mode,
        signal_band=signal_band_mode,
        basemap_enabled=basemap_enabled,
        basemap_mode=basemap_mode,
        basemap_cache_root=Path(basemap_cache_root) if basemap_cache_root else None,
        basemap_tile_server_url=basemap_tile_server_url or None,
        basemap_fallback_to_plain=cfg.TEC_MAP_BASEMAP_FALLBACK_TO_PLAIN,
        frame_dpi=int(dpi),
        basemap_alpha=float(basemap_alpha),
        vtec_layer_alpha=field_alpha,
        upsample_factor=int(upsample),
        color_min=color_min,
        color_max=color_max,
        show_accuracy=bool(show_accuracy),
    )

    try:
        frame_time, frame_summary = _load_single_frame_summary(
            data_root=data_root,
            year=year,
            doy=doy,
            date=date,
            timestamp=timestamp,
            stations=stations,
            pipeline=pipeline,
            field_mode=field_mode,
            log_label="frame",
        )
        if frame_summary.empty:
            raise FileNotFoundError("No samples found for the requested stations/timestamp.")

        image_bytes = build_frame_image_bytes(
            frame_summary=frame_summary,
            frame_time=frame_time,
            pipeline=pipeline,
            render=render,
            image_format=output_format,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("tec-map frame: rendering failed")
        raise HTTPException(status_code=500, detail=f"TEC map frame rendering failed: {exc}") from exc

    filename = f"tec_map_{field_mode}_{frame_time:%Y%m%d_%H%M}.{output_format}"
    return Response(
        content=image_bytes,
        media_type=FRAME_IMAGE_MEDIA_TYPES[output_format],
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.get("/tec-map/validate")
def tec_map_validate(
    current_user: User = Depends(get_current_user_or_401),
    year: int | None = Query(default=None, ge=2000, le=2100),
    doy: int | None = Query(default=None, ge=1, le=366),
    date: str | None = Query(default=None, description="Optional YYYY-MM-DD; overrides year/doy."),
    end_date: str | None = Query(default=None, description="Optional YYYY-MM-DD end day for multi-day validation ranges."),
    stations: list[str] = Query(..., min_length=1),
    start_time: str = Query(..., description="ISO timestamp or HH:MM:SS (UTC)."),
    end_time: str = Query(..., description="ISO timestamp or HH:MM:SS (UTC)."),
    min_elevation_deg: float = Query(default=20.0, ge=0.0, le=90.0),
    sampling_interval_seconds: int = Query(default=300, ge=1, le=3600),
    frame_minutes: int = Query(default=15, ge=1, le=240),
    ionosphere_height_km: float = Query(default=350.0, ge=50.0, le=2000.0),
    grid_resolution_deg: float = Query(default=1.0, gt=0.0, le=10.0),
    smoothing_sigma: float = Query(default=1.0, ge=0.0, le=20.0),
    interpolation: str = Query(
        default="both",
        description="Interpolation methods to validate: linear, kriging or both (default).",
    ),
    vtec_smooth_epochs: int = Query(
        default=0,
        ge=0,
        le=50,
        description="Rolling-median window in epochs per (station, satellite, arc). 0 disables.",
    ),
    normalize_stations: str = Query(
        default="off",
        description="Per-station VTEC median-shift: off (default), auto (only when MSTD bias failed), always.",
    ),
    format: str = Query(default="json", description="Response format: json (summary) or csv (per-point table)."),
):
    """
    Leave-one-station-out (LOSO) cross-validation of the TEC map — map-quality
    criterion #1 "accuracy at reference points". For every frame each station
    is excluded in turn, the field is predicted at its IPP from the remaining
    stations and the error statistics (bias/MAE/RMSE, TECU) are aggregated
    overall, per station and per frame. Only points inside the coverage radius
    of the remaining stations contribute to the headline metrics.
    """
    if not (getattr(current_user, "is_admin", False) or (hasattr(current_user, "can_access_page") and current_user.can_access_page("analysis"))):
        raise HTTPException(status_code=403, detail="Forbidden: you do not have access to the Analysis page.")

    data_root = _scan_root(cfg.PARQUET_OUTPUT_TECSUITE_DATA_PATH_CONTAINER, cfg.PARQUET_OUTPUT_TECSUITE_DATA_PATH_HOST)
    if not data_root:
        raise HTTPException(status_code=503, detail="TEC-suite parquet data root is not configured (PARQUET_OUTPUT_TECSUITE_DATA_PATH_*).")

    try:
        normalize_mode = _parse_normalize_stations(normalize_stations)
        interp_modes = _parse_validation_interpolations(interpolation)
        output_format = str(format or "json").strip().lower()
        if output_format not in {"json", "csv"}:
            raise ValueError("Unsupported format. Use one of: json, csv.")
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
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if len(stations) > TEC_MAP_MAX_STATIONS_PER_REQUEST:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Too many stations: {len(stations)} > {TEC_MAP_MAX_STATIONS_PER_REQUEST}. "
                "Reduce the station list or split the request."
            ),
        )
    duration_minutes = (range_end_dt - range_start_dt).total_seconds() / 60.0
    estimated_frames = int(math.ceil(duration_minutes / max(int(frame_minutes), 1)))
    if estimated_frames > TEC_MAP_MAX_FRAMES_PER_REQUEST:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Requested range would validate ~{estimated_frames} frames "
                f"(limit {TEC_MAP_MAX_FRAMES_PER_REQUEST}). Shorten the date range or "
                "increase frame_minutes."
            ),
        )

    pipeline = TecMapConfig(
        min_elevation_deg=float(min_elevation_deg),
        sampling_interval_seconds=int(sampling_interval_seconds),
        frame_minutes=int(frame_minutes),
        ionosphere_height_km=float(ionosphere_height_km),
        grid_resolution_deg=float(grid_resolution_deg),
        smoothing_sigma=float(smoothing_sigma),
        vtec_smooth_epochs=int(vtec_smooth_epochs),
        normalize_stations=normalize_mode,
    )

    request_started = time.monotonic()
    logger.info(
        "tec-map validate: %s..%s stations=%s methods=%s frames~%d",
        range_start_dt,
        range_end_dt,
        ",".join(stations),
        ",".join(interp_modes),
        estimated_frames,
    )

    try:
        found_stations = _validate_stations_for_range(
            root=Path(data_root),
            start_day=range_start_dt.normalize(),
            end_day=range_end_dt.normalize(),
            stations=stations,
        )
        frame_summary = _build_frame_summary_gif_range(
            root=Path(data_root),
            start_day=range_start_dt.normalize(),
            end_day=range_end_dt.normalize(),
            start_dt=range_start_dt,
            end_dt=range_end_dt,
            stations=found_stations,
            pipeline=pipeline,
        )
        if frame_summary.empty:
            raise FileNotFoundError("No samples found for the requested stations/time range.")

        cv_by_method = {
            method: loso_cross_validate(frame_summary, replace(pipeline, interpolation_method=method))
            for method in interp_modes
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("tec-map validate: cross-validation failed")
        raise HTTPException(status_code=500, detail=f"TEC map validation failed: {exc}") from exc

    logger.info("tec-map validate: done in %.1fs", time.monotonic() - request_started)

    if output_format == "csv":
        parts = []
        for method, cv in cv_by_method.items():
            cv = cv.copy()
            cv.insert(0, "method", method)
            parts.append(cv)
        csv_text = pd.concat(parts, ignore_index=True).to_csv(index=False)
        filename = f"tec_map_validation_{range_start_dt:%Y%m%d}.csv"
        return Response(
            content=csv_text,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return JSONResponse(
        content={
            "method": "leave-one-station-out",
            "config": {
                "start": range_start_dt.isoformat(),
                "end": range_end_dt.isoformat(),
                "stations": found_stations,
                "frame_minutes": int(frame_minutes),
                "min_elevation_deg": float(min_elevation_deg),
                "grid_resolution_deg": float(grid_resolution_deg),
                "coverage_radius_km": pipeline.ipp_gradient_radius_km,
                "normalize_stations": normalize_mode,
            },
            "results": {method: summarize_validation(cv) for method, cv in cv_by_method.items()},
        }
    )
