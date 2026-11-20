"""Assemble continuous satellite arcs and publish complete UTC-day outputs."""

import json
import math
import re
import shutil
import tempfile
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

import numpy as np
import pyarrow as pa
from numpy.typing import NDArray

from .dat import Track, read_track
from .output import MapPoint
from .parquet import FilteredArc, observation_batch, observation_schema, write_station_day
from .science import bandpass_arc, filter_arc, level_arc, shell_coordinates
from .settings import PeriodBand, VariationSettings

DAY_SECONDS = 86400


@dataclass
class DayReport:
    """Diagnostics for discovery, filtering and publication of one day."""

    day: str
    sites: list[str] = field(default_factory=list)
    input_files: int = 0
    rejected_samples: int = 0
    below_elevation_cutoff: int = 0
    short_arcs_by_band: dict[str, int] = field(default_factory=dict)
    undersampled_arcs_by_band: dict[str, int] = field(default_factory=dict)
    rows_by_station: dict[str, int] = field(default_factory=dict)
    missing_previous: int = 0
    missing_next: int = 0
    issues: list[str] = field(default_factory=list)
    epochs: int = 0
    maps: int = 0
    rows: int = 0
    points: int = 0
    error: str | None = None


def _folder(root: Path, day: date) -> Path:
    return root / f"{day.year:04d}" / f"{day.timetuple().tm_yday:03d}"


def _satellite_files(path: Path, site: str, day: date) -> dict[str, Path]:
    year, ordinal = day.year, day.timetuple().tm_yday
    pattern = re.compile(
        rf"{re.escape(site)}_([a-z][0-9]{{2,3}})_{ordinal:03d}_(?:{year:04d}|{year % 100:02d})\.dat", re.I
    )
    files = {}
    for source in sorted(path.glob("*")):
        if source.is_file() and (match := pattern.fullmatch(source.name)):
            satellite = match[1].upper()
            if satellite in files:
                raise ValueError(f"Multiple DAT files match {site} {satellite} in {path}")
            files[satellite] = source
    return files


def _selected(satellite: str, requested: str) -> bool:
    if requested == "ALL":
        return True
    if requested.isdecimal():
        return int(requested) in (0, int(satellite[1:]))
    return satellite[0] == requested[0] and int(satellite[1:]) == int(requested[1:])


def _arcs(
    track: Track, previous: Track | None, following: Track | None, settings: VariationSettings
) -> Iterator[NDArray[np.float64]]:
    parts = [track.samples]
    if previous is not None and math.isclose(previous.interval, track.interval):
        data = previous.samples[previous.samples[:, 0] < DAY_SECONDS].copy()
        data[:, 0] -= DAY_SECONDS
        parts.insert(0, data)
    if following is not None and math.isclose(following.interval, track.interval):
        data = following.samples[following.samples[:, 0] < DAY_SECONDS].copy()
        data[:, 0] += DAY_SECONDS
        if len(track.samples) and track.samples[-1, 0] == DAY_SECONDS:
            data = data[data[:, 0] > DAY_SECONDS]
        parts.append(data)
    merged = np.concatenate(parts)
    eligible = np.flatnonzero(merged[:, 1] >= settings.elevation_cutoff)
    samples = merged[eligible]
    gaps = np.diff(samples[:, 0])
    bad_gap = (gaps > (settings.max_absent_points + 1) * track.interval + 0.04) | (gaps < track.interval - 0.04)
    splits = np.flatnonzero(bad_gap | (np.diff(eligible) > 1) | (samples[1:, 4] != 0)) + 1
    yield from (arc for arc in np.split(samples, splits) if len(arc))


def _filter_observations(
    arc: NDArray[np.float64],
    track: Track,
    settings: VariationSettings,
    bands: tuple[PeriodBand, ...],
    report: DayReport,
) -> FilteredArc:
    seconds, elevation, azimuth, tec, _ = arc.T
    lat, lon, factor = shell_coordinates(elevation, azimuth, track.latitude, track.longitude, settings.shell_height_km)
    variations = {band.column: np.full(len(arc), np.nan) for band in bands}
    result = FilteredArc(arc, lat, lon, factor, variations, np.full(len(arc), np.nan))
    legacy = settings.filter == "legacy"
    skip = settings.skip_edge_samples if legacy else 0
    region = slice(skip, len(arc) - skip)
    cadence = track.interval
    regular = np.empty(0)
    steps = np.empty(0, dtype=int)
    if len(arc[region]):
        steps = np.rint((seconds[region] - seconds[skip]) / cadence).astype(int)
        slant = tec[region]
        values = level_arc(slant, factor[region], settings.minimum_tec, bool(settings.vertical)) if legacy else slant
        regular = np.interp(np.arange(steps[-1] + 1), steps, values)
    for band in bands:
        column = band.column
        if legacy:
            if len(regular) >= max(3, int(band.longest_minutes * 60 / cadence)):
                smooth, variation = filter_arc(regular, cadence, band.shortest_minutes, band.longest_minutes)
                variations[column][region] = variation[steps]
                if band == bands[0]:
                    result.smoothed_tec[region] = smooth[steps]
        elif band.shortest_minutes * 60 <= 2 * cadence:
            report.undersampled_arcs_by_band[column] += 1
            continue
        else:
            variation = bandpass_arc(regular, cadence, band)[steps]
            variations[column][region] = variation * factor[region] if settings.vertical else variation
        if not np.isfinite(variations[column]).any():
            report.short_arcs_by_band[column] += 1
    return result


def _map_points(
    arc: FilteredArc, track: Track, settings: VariationSettings, epochs: set[int], site: str, filename: str
) -> Iterator[tuple[int, MapPoint]]:
    seconds, elevation, azimuth, _, _ = arc.samples.T
    variation = next(iter(arc.variations.values()))
    rows = (
        (np.abs(variation) >= settings.di_threshold)
        & (seconds >= 0)
        & (seconds < DAY_SECONDS)
        & (settings.min_lat <= arc.latitude)
        & (arc.latitude <= settings.max_lat)
        & (settings.min_lon <= arc.longitude)
        & (arc.longitude <= settings.max_lon)
    )
    for index in np.flatnonzero(rows):
        value = seconds[index]
        epoch = round(value)
        if abs(value - epoch) <= 0.019 and epoch in epochs:
            yield (
                epoch,
                MapPoint(
                    arc.latitude[index],
                    arc.longitude[index],
                    variation[index],
                    arc.smoothed_tec[index],
                    azimuth[index],
                    elevation[index],
                    track.latitude,
                    (track.longitude + 180) % 360 - 180,
                    site,
                    filename,
                ),
            )


def _load_neighbor(
    files: dict[str, Path], satellite: str, track: Track, parameter: str, report: DayReport
) -> Track | None:
    source = files.get(satellite)
    if source is None:
        return None
    try:
        neighbor = read_track(source, parameter)
    except (OSError, ValueError) as exc:
        report.issues.append(str(exc))
        return None
    if not len(neighbor.samples) or not math.isclose(track.interval, neighbor.interval):
        report.issues.append(f"No compatible observations in neighboring file {source}")
        return None
    return neighbor


def collect_day(
    staging: Path,
    root: Path,
    day: date,
    sites: list[str],
    epochs: set[int],
    settings: VariationSettings,
    previous_root: Path | None,
    next_root: Path | None,
    report: DayReport,
) -> dict[int, list[MapPoint]]:
    """Write station tables and collect requested optional map points."""
    folder = _folder(root, day)
    if not folder.is_dir():
        raise ValueError(f"No observations found because the input day is missing at {folder}")
    report.sites = sites or sorted(p.name for p in folder.iterdir() if p.is_dir())
    bands = settings.period_bands
    schema = observation_schema(settings, bands)
    report.short_arcs_by_band = dict.fromkeys((band.column for band in bands), 0)
    report.undersampled_arcs_by_band = dict.fromkeys(report.short_arcs_by_band, 0)
    has_observations = False
    previous_day, next_day = day - timedelta(days=1), day + timedelta(days=1)
    points: dict[int, list[MapPoint]] = defaultdict(list)
    for site in report.sites:
        batches = []
        arc_id = 0
        files = _satellite_files(folder / site, site, day)
        previous = (
            _satellite_files(_folder(previous_root, previous_day) / site, site, previous_day) if previous_root else {}
        )
        following = _satellite_files(_folder(next_root, next_day) / site, site, next_day) if next_root else {}
        selected = [
            (satellite, source) for satellite, source in files.items() if _selected(satellite, settings.satellite)
        ]
        if not selected:
            report.issues.append(f"No selected DAT files for {site} on {day}")
        for satellite, source in selected:
            report.input_files += 1
            try:
                track = read_track(source, settings.tec_parameter)
            except (OSError, ValueError) as exc:
                report.issues.append(str(exc))
                continue
            report.rejected_samples += track.rejected
            if not len(track.samples):
                continue
            has_observations = True
            report.below_elevation_cutoff += int(np.count_nonzero(track.samples[:, 1] < settings.elevation_cutoff))
            before = _load_neighbor(previous, satellite, track, settings.tec_parameter, report)
            after = _load_neighbor(following, satellite, track, settings.tec_parameter, report)
            report.missing_previous += previous_root is not None and before is None
            report.missing_next += next_root is not None and after is None
            for arc in _arcs(track, before, after, settings):
                if not np.any((arc[:, 0] >= 0) & (arc[:, 0] < DAY_SECONDS)):
                    continue
                arc_id += 1
                filtered = _filter_observations(arc, track, settings, bands, report)
                batches.append(observation_batch(day, site, satellite, arc_id, filtered, schema))
                if epochs:
                    for epoch, point in _map_points(filtered, track, settings, epochs, site, source.name):
                        points[epoch].append(point)
        table = pa.Table.from_batches(batches, schema=schema)
        write_station_day(staging, day, site, table)
        report.rows_by_station[site] = table.num_rows
    if not has_observations:
        raise ValueError("No valid DAT observations found for the selected stations and satellites")
    report.rows = sum(report.rows_by_station.values())
    return points


@contextmanager
def stage_day(output: Path, day: date, overwrite: bool) -> Iterator[Path]:
    """Stage all assets on the destination filesystem and publish them together."""
    target = _folder(output, day)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = target.with_name(f".{target.name}.tec-variations-lock")
    try:
        lock.mkdir()
    except FileExistsError as exc:
        raise ValueError(f"Another writer holds the TEC Variations lock for {target}") from exc
    try:
        if target.exists() or target.is_symlink():
            if not overwrite:
                raise ValueError(f"Output already exists at {target}, use --overwrite to replace it")
            if target.is_symlink() or not (target / "diagnostics.json").is_file():
                raise ValueError(f"Refusing to overwrite a directory without TEC Variations diagnostics at {target}")
        with tempfile.TemporaryDirectory(prefix=f".{target.name}.tec-variations-", dir=target.parent) as temporary:
            staging = Path(temporary) / "day"
            staging.mkdir()
            yield staging
            backup = lock / "previous"
            if target.exists():
                target.rename(backup)
            try:
                staging.rename(target)
            except OSError:
                if backup.exists():
                    backup.rename(target)
                raise
            if backup.exists():
                shutil.rmtree(backup)
    finally:
        if not (lock / "previous").exists():
            lock.rmdir()


def diagnostics_json(report: DayReport, settings: VariationSettings) -> str:
    """Serialize run counts and configuration for troubleshooting."""
    return json.dumps({"converter": "tec-variations", **asdict(report), "settings": asdict(settings)}, indent=2) + "\n"


def write_failure(output: Path, day: date, report: DayReport, settings: VariationSettings) -> None:
    """Keep failed attempts separate from previously published days."""
    target = _folder(output, day)
    target.parent.mkdir(parents=True, exist_ok=True)
    path = target.with_name(f"{target.name}.failed-{uuid4().hex}.json")
    path.write_text(diagnostics_json(report, settings), encoding="utf-8")
