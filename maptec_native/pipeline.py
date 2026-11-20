"""Assemble satellite arcs and publish one complete map directory per day."""

import json
import math
import re
import shutil
import tempfile
from collections import defaultdict
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

import numpy as np
from numpy.typing import NDArray

from .dat import Track, read_track
from .output import MapPoint, MapRenderer
from .science import filter_arc, level_arc, shell_coordinates
from .settings import MapSettings

DAY_SECONDS = 86400


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
    track: Track, previous: Track | None, following: Track | None, allowed_gaps: int
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
    gaps = np.diff(merged[:, 0])
    bad_gap = (gaps > (allowed_gaps + 1) * track.interval + 0.04) | (gaps < track.interval - 0.04)
    splits = np.flatnonzero(bad_gap | (merged[1:, 4] != 0)) + 1
    yield from (arc for arc in np.split(merged, splits) if len(arc))


def _filter_points(
    arc: NDArray[np.float64], track: Track, settings: MapSettings, epochs: set[int], site: str, filename: str
) -> Iterator[tuple[int, MapPoint]]:
    edge = settings.skip_edge_samples
    arc = arc[edge : len(arc) - edge]
    if not len(arc):
        return
    seconds, elevation, azimuth, tec, _ = arc.T
    steps = np.rint((seconds - seconds[0]) / track.interval).astype(int)
    if steps[-1] + 1 < max(3, int(settings.detrend_minutes * 60 / track.interval)):
        return
    should_map = [
        (i, round(value))
        for i, value in enumerate(seconds)
        if abs(value - round(value)) <= 0.019 and round(value) in epochs
    ]
    if not should_map:
        return
    lat, lon, factor = shell_coordinates(elevation, azimuth, track.latitude, track.longitude, settings.shell_height_km)
    tec = level_arc(tec, factor, settings.minimum_tec, bool(settings.vertical))
    regular = np.interp(np.arange(steps[-1] + 1), steps, tec)
    smooth, variation = filter_arc(regular, track.interval, settings.smoothing_minutes, settings.detrend_minutes)
    for index, epoch in should_map:
        step = steps[index]
        if (
            abs(variation[step]) >= settings.di_threshold
            and settings.min_lat <= lat[index] <= settings.max_lat
            and settings.min_lon <= lon[index] <= settings.max_lon
        ):
            yield (
                epoch,
                MapPoint(
                    lat[index],
                    lon[index],
                    variation[step],
                    smooth[step],
                    azimuth[index],
                    elevation[index],
                    track.latitude,
                    (track.longitude + 180) % 360 - 180,
                    site,
                    filename,
                ),
            )


@dataclass
class DayReport:
    """Diagnostics for discovery, filtering and publication of one day."""

    day: str
    sites: list[str] = field(default_factory=list)
    input_files: int = 0
    rejected_samples: int = 0
    missing_previous: int = 0
    missing_next: int = 0
    issues: list[str] = field(default_factory=list)
    epochs: int = 0
    maps: int = 0
    points: int = 0
    error: str | None = None


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


def collect_points(
    root: Path,
    day: date,
    sites: list[str],
    epochs: set[int],
    settings: MapSettings,
    previous_root: Path | None,
    next_root: Path | None,
    report: DayReport,
) -> dict[int, list[MapPoint]]:
    """Collect observations from the requested day, with optional splicing."""
    folder = _folder(root, day)
    if not folder.is_dir():
        raise ValueError(f"Nothing to map because the input day is missing at {folder}")
    report.sites = sites or sorted(p.name for p in folder.iterdir() if p.is_dir())
    previous_day, next_day = day - timedelta(days=1), day + timedelta(days=1)
    points: dict[int, list[MapPoint]] = defaultdict(list)
    for site in report.sites:
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
            before = _load_neighbor(previous, satellite, track, settings.tec_parameter, report)
            after = _load_neighbor(following, satellite, track, settings.tec_parameter, report)
            report.missing_previous += previous_root is not None and before is None
            report.missing_next += next_root is not None and after is None
            for arc in _arcs(track, before, after, settings.max_absent_points):
                for epoch, point in _filter_points(arc, track, settings, epochs, site, source.name):
                    points[epoch].append(point)
    return points


def publish_day(
    output: Path,
    day: date,
    points: dict[int, list[MapPoint]],
    diagnostics: str,
    renderer: MapRenderer,
    overwrite: bool,
    progress: Callable[[int], None],
) -> None:
    """Stage all assets on the destination filesystem and publish them together."""
    target = _folder(output, day)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = target.with_name(f".{target.name}.maptec-lock")
    try:
        lock.mkdir()
    except FileExistsError as exc:
        raise ValueError(f"Another writer holds the MapTEC lock for {target}") from exc
    try:
        if target.exists() or target.is_symlink():
            if not overwrite:
                raise ValueError(f"Output already exists at {target}, use --overwrite to replace it")
            if target.is_symlink() or not (target / "diagnostics.json").is_file():
                raise ValueError(f"Refusing to overwrite a directory without MapTEC diagnostics at {target}")
        with tempfile.TemporaryDirectory(prefix=f".{target.name}.maptec-", dir=target.parent) as temporary:
            staging = Path(temporary) / "maps"
            staging.mkdir()
            for count, (epoch, rows) in enumerate(sorted(points.items()), 1):
                renderer.write(staging, day, epoch, rows)
                progress(count)
            (staging / "diagnostics.json").write_text(diagnostics, encoding="utf-8")
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


def diagnostics_json(report: DayReport, settings: MapSettings) -> str:
    """Serialize run counts and configuration for troubleshooting."""
    return json.dumps({"converter": "maptec-native", **asdict(report), "settings": asdict(settings)}, indent=2) + "\n"


def write_failure(output: Path, day: date, report: DayReport, settings: MapSettings) -> None:
    """Keep failed attempts separate from previously published maps."""
    target = _folder(output, day)
    target.parent.mkdir(parents=True, exist_ok=True)
    path = target.with_name(f"{target.name}.failed-{uuid4().hex}.json")
    path.write_text(diagnostics_json(report, settings), encoding="utf-8")
