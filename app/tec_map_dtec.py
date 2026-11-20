"""Serve converter dTEC availability, snapshots and background animations."""

from __future__ import annotations

import json
import logging
import math
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import numpy as np
import pandas as pd
import pyarrow.dataset as ds
import pyarrow.parquet as pq
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app import config, tec_map_jobs
from app.auth import get_current_user_or_401
from app.models import User
from app.tec_map import (
    _STATION_NAME_RE,
    TEC_MAP_MAX_STATIONS_PER_REQUEST,
    _day_of_year,
    _parse_animation_format,
    _parse_basemap_mode,
    _parse_render_quality,
)
from app.tec_map_dtec_render import DtecFigure, render_animation, snapshot_json
from app.tec_map_jobs import RenderResult
from app.tec_map_parallel import Progress
from app.tec_map_render import ANIMATION_MEDIA_TYPES, TecMapRenderConfig, bounds_for_ipp, choose_animation_dpi
from tec_variations.settings import PeriodBand

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/tec-map/dtec", tags=["tec-map"])

MAX_FRAMES = 1440
NO_SAMPLES = "No dTEC samples found for the requested stations and time"
BAND_COLUMN = re.compile(r"dtec_(\d+(?:p\d+)?)_(\d+(?:p\d+)?)m")
OBSERVATION_COLUMNS = {
    "datetime_utc": "datetime_utc",
    "station": "station",
    "satellite": "satellite",
    "elevation_deg": "elevation_deg",
    "ipp_latitude_deg": "ipp_lat",
    "ipp_longitude_deg": "ipp_lon",
}


@dataclass(frozen=True)
class DtecFiles:
    """Station tables and projected columns for a UTC observation window."""

    paths: tuple[Path, ...]
    start: datetime
    end: datetime
    band: PeriodBand
    shell_height_km: float

    @property
    def time_filter(self) -> Any:
        return (ds.field("datetime_utc") >= self.start) & (ds.field("datetime_utc") <= self.end)


@dataclass(frozen=True)
class DtecOptions:
    """Validated settings shared by snapshot and range requests."""

    stations: tuple[str, ...]
    band: str
    step_seconds: int
    color_limit: float | None
    terminator: bool
    day: date | None


def _require_access(current_user: User = Depends(get_current_user_or_401)) -> User:
    if not (current_user.is_admin or current_user.can_access_page("analysis")):
        raise HTTPException(403, "You do not have access to the Analysis page")
    return current_user


def _root() -> Path:
    value = config.TEC_VARIATIONS_OUTPUT_DATA_PATH_CONTAINER.strip()
    root = Path(value)
    if not value or not root.is_dir():
        raise HTTPException(404, "TEC Variations output is not configured")
    return root


def _day(date_text: str, year: str, doy: str) -> date | None:
    try:
        if date_text:
            return date.fromisoformat(date_text)
        if year or doy:
            return _day_of_year(int(year), int(doy)).date()
    except (ValueError, OverflowError) as exc:
        raise HTTPException(400, "Provide an ISO date or a valid year and day of year") from exc
    return None


def _utc_time(value: str, day: date | None) -> datetime:
    try:
        if "T" in value or " " in value:
            timestamp = datetime.fromisoformat(value)
        elif day is not None:
            timestamp = datetime.combine(day, time.fromisoformat(value))
        else:
            raise ValueError
        return timestamp.replace(tzinfo=UTC) if timestamp.tzinfo is None else timestamp.astimezone(UTC)
    except (ValueError, OverflowError) as exc:
        raise HTTPException(400, "Provide a UTC clock time with a date or a full ISO timestamp") from exc


def _number(value: str, low: float, high: float, message: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise HTTPException(400, message) from exc
    if not low <= number <= high:
        raise HTTPException(400, message)
    return number


def _options(
    stations: list[str] | None = Query(None),
    band: str = "",
    step_seconds: str = "60",
    color_limit: str = "auto",
    terminator: str = "false",
    date: str = "",
    year: str = "",
    doy: str = "",
) -> DtecOptions:
    selected = tuple(
        sorted({token for value in stations or [] for token in re.split(r"[,;\s]+", value.lower()) if token})
    )
    if not selected:
        raise HTTPException(400, "Select at least one station")
    if any(not _STATION_NAME_RE.fullmatch(station) for station in selected):
        raise HTTPException(400, "Invalid station name")
    if len(selected) > TEC_MAP_MAX_STATIONS_PER_REQUEST:
        raise HTTPException(413, f"Request exceeds the limit of {TEC_MAP_MAX_STATIONS_PER_REQUEST} stations")
    if not BAND_COLUMN.fullmatch(band):
        raise HTTPException(400, "Unknown dTEC band")
    if step_seconds not in {"30", "60", "120", "300"}:
        raise HTTPException(400, "Frame step must be 30, 60, 120 or 300 seconds")
    if terminator not in {"false", "true", "0", "1"}:
        raise HTTPException(400, "Terminator must be true or false")
    limit = None
    if color_limit.lower() != "auto":
        try:
            limit = float(color_limit)
            if not math.isfinite(limit) or limit <= 0:
                raise ValueError
        except ValueError as exc:
            raise HTTPException(400, "Color limit must be Auto or a positive finite value in TECU") from exc
    return DtecOptions(selected, band, int(step_seconds), limit, terminator in {"true", "1"}, _day(date, year, doy))


def _station_file(root: Path, day: date, station: str) -> Path:
    return root / f"{day:%Y/%j}" / station / f"{station}_dtec_{day:%j_%y}.parquet"


def _day_files(root: Path, day: date) -> Iterator[tuple[str, Path]]:
    directory = root / f"{day:%Y/%j}"
    if not directory.is_dir():
        return
    for entry in directory.iterdir():
        if _STATION_NAME_RE.fullmatch(entry.name):
            path = _station_file(root, day, entry.name)
            if path.is_file():
                yield entry.name, path


def _schema_bands(schema: Any) -> dict[str, PeriodBand]:
    metadata = schema.metadata or {}
    if b"bands_minutes" in metadata:
        try:
            bands = [PeriodBand(*map(float, pair)) for pair in json.loads(metadata[b"bands_minutes"])]
            available = {band.column: band for band in bands if band.column in schema.names}
            if available:
                return available
        except (TypeError, ValueError, ArithmeticError):
            pass
    return {
        name: PeriodBand(*(float(value.replace("p", ".")) for value in match.groups()))
        for name in schema.names
        if (match := BAND_COLUMN.fullmatch(name))
    }


@router.get("/availability")
def availability(
    date: str = "",
    year: str = "",
    doy: str = "",
    current_user: User = Depends(_require_access),
) -> dict[str, Any]:
    """List UTC dates or the converter stations and period bands for one day."""
    day = _day(date, year, doy)
    root = _root()
    if day is not None:
        files = dict(_day_files(root, day))
        if not files:
            raise HTTPException(404, "No dTEC output found for the requested day")
        bands = {band for path in files.values() for band in _schema_bands(pq.read_schema(path)).values()}
        return {
            "stations": sorted(files),
            "bands": [
                {"column": band.column, "minutes": [band.shortest_minutes, band.longest_minutes]}
                for band in sorted(bands, key=lambda band: (band.shortest_minutes, band.longest_minutes))
            ],
        }

    days_by_year: dict[int, list[int]] = {}
    for folder in root.iterdir():
        if not folder.is_dir() or not re.fullmatch(r"[0-9]{4}", folder.name):
            continue
        days = []
        for entry in folder.iterdir():
            if not entry.is_dir() or not re.fullmatch(r"[0-9]{3}", entry.name):
                continue
            try:
                partition_day = _day_of_year(int(folder.name), int(entry.name)).date()
            except (ValueError, OverflowError):
                continue
            if next(_day_files(root, partition_day), None) is not None:
                days.append(int(entry.name))
        if days:
            days_by_year[int(folder.name)] = sorted(days)
    return {"years": sorted(days_by_year), "doysByYear": days_by_year}


def select_files(root: Path, stations: tuple[str, ...], start: datetime, end: datetime, band: str) -> DtecFiles:
    """Inspect only the station files overlapping the requested UTC window."""
    day_count = (end.date() - start.date()).days + 1
    paths = []
    heights = set()
    found = False
    selected_band = None
    for offset in range(day_count):
        day = start.date() + timedelta(days=offset)
        for station in stations:
            path = _station_file(root, day, station)
            if not path.is_file():
                continue
            found = True
            schema = pq.read_schema(path)
            period = _schema_bands(schema).get(band)
            if period is None:
                continue
            try:
                height = float((schema.metadata or {}).get(b"shell_height_km", b"nan"))
                if not math.isfinite(height) or height <= 0:
                    raise ValueError
            except ValueError as exc:
                raise HTTPException(400, "The selected dTEC output has invalid shell-height metadata") from exc
            heights.add(height)
            selected_band = period
            paths.append(path)
    if not found:
        raise HTTPException(404, "No dTEC output found for the requested stations and dates")
    if selected_band is None:
        raise HTTPException(400, "Unknown dTEC band")
    if len(heights) != 1:
        raise HTTPException(400, "The selected dTEC files must use the same shell height")
    return DtecFiles(tuple(paths), start, end, selected_band, heights.pop())


def read_observations(files: DtecFiles) -> pd.DataFrame:
    """Read the selected band and geometry using Parquet time predicates."""
    columns = OBSERVATION_COLUMNS | {files.band.column: "dtec"}
    tables = [
        pq.read_table(path, columns=list(columns), filters=files.time_filter, partitioning=None).to_pandas()
        for path in files.paths
    ]
    return pd.concat(tables, ignore_index=True).rename(columns=columns)


def _has_observations(files: DtecFiles) -> bool:
    band = ds.field(files.band.column)
    predicate = files.time_filter & (band > -math.inf) & (band < math.inf)
    return any(
        ds.dataset(path, format="parquet").head(1, columns=[], filter=predicate).num_rows for path in files.paths
    )


def build_frames(
    observations: pd.DataFrame, start: datetime, step_seconds: int, frame_count: int
) -> dict[datetime, pd.DataFrame]:
    """Pick the nearest sample of every link per frame, preferring the earlier sample at ties."""
    step = timedelta(seconds=step_seconds)
    times = [start + index * step for index in range(frame_count)]
    frame_us = pd.DatetimeIndex(times).as_unit("us").asi8
    tolerance_us = step_seconds * 500_000
    picks: list[list[int]] = [[] for _ in times]
    ordered = observations.sort_values("datetime_utc", kind="stable").reset_index(drop=True)
    for _, link in ordered.groupby(["station", "satellite"], sort=True):
        sample_us = pd.DatetimeIndex(link["datetime_utc"]).as_unit("us").asi8
        insertion = np.searchsorted(sample_us, frame_us)
        right = insertion.clip(0, len(sample_us) - 1)
        left = (insertion - 1).clip(0, len(sample_us) - 1)
        nearest = np.where(np.abs(frame_us - sample_us[left]) <= np.abs(frame_us - sample_us[right]), left, right)
        valid = np.abs(frame_us - sample_us[nearest]) <= tolerance_us
        rows = link.index.to_numpy()[nearest[valid]]
        for index, row in zip(np.flatnonzero(valid), rows, strict=True):
            picks[index].append(row)
    frames = {}
    for frame_time, positions in zip(times, picks, strict=True):
        frame = ordered.iloc[positions]
        frames[frame_time] = frame.loc[np.isfinite(frame["dtec"])]
    return frames


def color_limits(values: np.ndarray, limit: float | None) -> tuple[float, float]:
    """Use one diverging scale across all frames in a request."""
    if limit is None:
        magnitude = np.abs(values[np.isfinite(values)])
        limit = max(0.01, float(np.quantile(magnitude, 0.98))) if magnitude.size else 0.01
    return -limit, limit


def prepare_frames(
    files: DtecFiles, options: DtecOptions, start: datetime, count: int
) -> tuple[DtecFigure, dict[datetime, pd.DataFrame]]:
    """Read the window and compute shared figure bounds and color limits."""
    observations = read_observations(files)
    values = observations["dtec"].to_numpy()
    if not np.isfinite(values).any():
        raise FileNotFoundError(NO_SAMPLES)
    frames = build_frames(observations, start, options.step_seconds, count)
    limits = color_limits(values, options.color_limit)
    logger.info("dTEC map: read %d rows for %d frames with color limit %.6g TECU", len(observations), count, limits[1])
    return DtecFigure(
        files.band, files.shell_height_km, bounds_for_ipp(observations), limits, options.terminator
    ), frames


def _window(options: DtecOptions, start: datetime, end: datetime) -> DtecFiles:
    margin = timedelta(seconds=options.step_seconds / 2)
    try:
        lower, upper = start - margin, end + margin
    except OverflowError as exc:
        raise HTTPException(400, "The requested UTC window exceeds the supported date range") from exc
    return select_files(_root(), options.stations, lower, upper, options.band)


@router.get("/snapshot")
def snapshot(
    timestamp: str = "",
    current_user: User = Depends(_require_access),
    options: DtecOptions = Depends(_options),
) -> dict[str, Any]:
    """Build the interactive dTEC figure for one UTC instant."""
    instant = _utc_time(timestamp, options.day)
    files = _window(options, instant, instant)
    try:
        figure, frames = prepare_frames(files, options, instant, 1)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return snapshot_json(figure, instant, frames[instant])


@router.post("/gif/jobs", status_code=202)
def start_job(
    request: Request,
    start_time: str = "",
    end_time: str = "",
    end_date: str = "",
    format: str = "gif",
    quality: str = "standard",
    basemap: str = "off",
    basemap_alpha: str = "0.28",
    frame_dpi: str = "",
    current_user: User = Depends(_require_access),
    options: DtecOptions = Depends(_options),
) -> dict[str, Any]:
    """Validate a range and submit drawing and encoding to the shared job queue."""
    start = _utc_time(start_time, options.day)
    end = _utc_time(end_time, _day(end_date, "", "") or options.day)
    if end < start and not end_date and "T" not in end_time and " " not in end_time:
        try:
            end += timedelta(days=1)
        except OverflowError as exc:
            raise HTTPException(400, "The requested UTC window exceeds the supported date range") from exc
    if end < start:
        raise HTTPException(400, "Range end must be on or after the start")
    step = timedelta(seconds=options.step_seconds)
    count = max(1, math.ceil((end - start) / step))
    if count > MAX_FRAMES:
        raise HTTPException(413, f"Request exceeds the limit of {MAX_FRAMES} frames")
    try:
        animation_format = _parse_animation_format(format)
        high_quality = _parse_render_quality(quality) == "high"
        basemap_mode = _parse_basemap_mode(basemap)
    except ValueError as exc:
        raise HTTPException(400, str(exc).split(". ")[0]) from exc
    alpha = _number(basemap_alpha, 0, 1, "Basemap opacity must be between 0 and 1")
    dpi = (
        int(_number(frame_dpi, 50, 300, "Frame DPI must be between 50 and 300"))
        if frame_dpi
        else choose_animation_dpi(animation_format, high_quality, count)
    )
    render = TecMapRenderConfig(
        basemap_enabled=basemap_mode != "off",
        basemap_mode=basemap_mode,
        basemap_alpha=alpha,
        basemap_cache_root=Path(config.TEC_MAP_BASEMAP_CACHE_ROOT),
        basemap_tile_server_url=config.TEC_MAP_BASEMAP_TILE_SERVER_URL,
        basemap_fallback_to_plain=config.TEC_MAP_BASEMAP_FALLBACK_TO_PLAIN,
        animation_format=animation_format,
        frame_dpi=dpi,
        gif_high_quality=high_quality,
    )
    files = _window(options, start, start + step * (count - 1))
    if not _has_observations(files):
        raise HTTPException(404, NO_SAMPLES)

    def work(progress: Progress) -> RenderResult:
        progress("loading", 0, 0)
        figure, frames = prepare_frames(files, options, start, count)
        content = render_animation(figure, frames, render, progress)
        return RenderResult(content, ANIMATION_MEDIA_TYPES[animation_format], f"dtec_map.{animation_format}")

    key = "dtec:" + urlencode(sorted(request.query_params.multi_items()))
    try:
        job = tec_map_jobs.submit(current_user.id, key, work)
    except tec_map_jobs.JobLimitError as exc:
        raise HTTPException(429, str(exc).rstrip(".")) from exc
    return tec_map_jobs.describe(job)
