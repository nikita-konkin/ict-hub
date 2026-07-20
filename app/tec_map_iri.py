"""
tec_map_iri.py — IRI model VTEC (via PyIRI) for comparison with empirical maps.

Provides the climatological reference field for the TEC map service:

  * ``iri_vtec_lookup``      — model VTEC on an arbitrary set of points for one
    day at the requested UT hours (one PyIRI whole-day evaluation per day);
  * ``iri_vtec_grid_for_frames`` — per-frame 2-D VTEC grids matching the
    empirical render grid (``model=iri`` / ``model=difference`` map modes);
  * ``iri_vtec_for_rows``    — row-wise model VTEC for the per-station series
    export (one value per (frame_time, IPP) row).

The only external input IRI needs here is the daily adjusted F10.7 solar flux.
It is fetched automatically from spaceweather.gc.ca (same source and table
format as the MUF/foF2 ML pipelines), cached on disk under /app/data so
repeated requests and offline restarts do not depend on the network, and falls
back to a configurable default when neither the network nor the cache has the
requested day.

IRI is a monthly-median climatological model: it reproduces the background and
the diurnal course, not ionospheric weather. The empirical branch produces
relative (MSTD-levelled) VTEC with residual inter-station biases, so
difference maps are expected to carry a systematic offset — the per-frame
bias is reported alongside RMSE instead of being silently removed.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import date, datetime
from pathlib import Path
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Daily adjusted F10.7 table (fluxdate … fluxadjflux); column 6 is the
# adjusted flux, same parsing convention as the ionosphere ML pipelines.
F107_FLUXTABLE_URL = "https://spaceweather.gc.ca/solar_flux_data/daily_flux_values/fluxtable.txt"
F107_CACHE_PATH = Path(os.getenv("TEC_MAP_F107_CACHE_PATH", "/app/data/f107_fluxtable.txt"))
F107_CACHE_MAX_AGE_HOURS = float(os.getenv("TEC_MAP_F107_CACHE_MAX_AGE_HOURS", "72"))
F107_DEFAULT = float(os.getenv("TEC_MAP_F107_DEFAULT", "150.0"))
HTTP_TIMEOUT_SECONDS = 60

# Altitude grid for the EDP -> VTEC integration [km]: dense through the F
# region where the density peaks, coarser above. PyIRI integrates with a
# trapezoid over exactly this grid, so spacing directly controls accuracy.
IRI_ALT_GRID_KM = np.concatenate(
    [
        np.arange(90.0, 500.0, 5.0),
        np.arange(500.0, 1000.0, 25.0),
        np.arange(1000.0, 2000.0 + 1.0, 100.0),
    ]
)

# UT hours per PyIRI evaluation chunk. The intermediate EDP array is
# (n_ut, n_alt, n_points) float64; chunking keeps it tens of MB at most
# even for a full day of 15-minute frames over the render grid.
_UT_CHUNK_SIZE = 12

_MODEL_CACHE_MAX_ENTRIES = 16
_model_cache: dict[tuple, np.ndarray] = {}
_model_cache_lock = threading.Lock()

_f107_fetch_lock = threading.Lock()
_f107_memory: tuple[float, dict[date, float]] | None = None  # (cache file mtime, records)


def parse_f107_adjusted_flux_records(raw_table: str) -> dict[date, float]:
    """Parse the spaceweather.gc.ca fluxtable.txt into {date: adjusted F10.7}."""
    records: dict[date, float] = {}
    for raw_line in raw_table.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("fluxdate") or set(line) == {"-"}:
            continue
        parts = line.split()
        if len(parts) < 6:
            continue
        try:
            flux_date = datetime.strptime(parts[0], "%Y%m%d").date()
            records[flux_date] = float(parts[5])
        except ValueError:
            continue
    if not records:
        raise ValueError("Could not parse any adjusted F10.7 values from the flux table.")
    return records


def _fetch_fluxtable_text() -> str:
    request = Request(F107_FLUXTABLE_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        return response.read().decode("utf-8", errors="replace")


def _load_f107_records(allow_fetch: bool = True) -> dict[date, float]:
    """
    F10.7 records from the disk cache, refreshing it from the network when the
    cache is missing or older than F107_CACHE_MAX_AGE_HOURS. A stale cache is
    still used when the network is unavailable.
    """
    global _f107_memory

    cache_path = F107_CACHE_PATH
    cache_mtime = cache_path.stat().st_mtime if cache_path.exists() else 0.0
    cache_age_hours = (time.time() - cache_mtime) / 3600.0 if cache_mtime else float("inf")

    if allow_fetch and cache_age_hours > F107_CACHE_MAX_AGE_HOURS:
        with _f107_fetch_lock:
            # Another thread may have refreshed the cache while we waited.
            cache_mtime = cache_path.stat().st_mtime if cache_path.exists() else 0.0
            cache_age_hours = (time.time() - cache_mtime) / 3600.0 if cache_mtime else float("inf")
            if cache_age_hours > F107_CACHE_MAX_AGE_HOURS:
                try:
                    raw = _fetch_fluxtable_text()
                    parse_f107_adjusted_flux_records(raw)  # validate before persisting
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache_path.write_text(raw, encoding="utf-8")
                    cache_mtime = cache_path.stat().st_mtime
                    logger.info("tec-map iri: refreshed F10.7 flux table from %s", F107_FLUXTABLE_URL)
                except Exception as exc:
                    logger.warning("tec-map iri: F10.7 fetch failed (%s); using cache/fallback", exc)

    if not cache_path.exists():
        return {}
    if _f107_memory is not None and _f107_memory[0] == cache_mtime:
        return _f107_memory[1]
    try:
        records = parse_f107_adjusted_flux_records(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("tec-map iri: unreadable F10.7 cache %s (%s)", cache_path, exc)
        return {}
    _f107_memory = (cache_mtime, records)
    return records


def resolve_f107_for_day(day: date, override: float | None = None) -> tuple[float, str]:
    """
    Adjusted F10.7 for `day` and where it came from:
    'user' (explicit override), 'observed' (day match in the table),
    'nearest' (closest earlier day in the table), 'default' (fallback constant).
    """
    if override is not None:
        return float(override), "user"

    records = _load_f107_records()
    if day in records:
        return records[day], "observed"
    earlier = [record_day for record_day in records if record_day <= day]
    if earlier:
        nearest_day = max(earlier)
        # Only trust a nearby proxy: the 27-day solar rotation makes older
        # values progressively less representative.
        if (day - nearest_day).days <= 40:
            return records[nearest_day], "nearest"
    logger.warning("tec-map iri: no F10.7 for %s; using default %.1f", day, F107_DEFAULT)
    return F107_DEFAULT, "default"


def _import_pyiri():
    try:
        import PyIRI
        import PyIRI.main_library as ml
    except ImportError as exc:  # pragma: no cover - dependency is in requirements
        raise RuntimeError(
            "PyIRI is not installed in this environment; the IRI model modes are unavailable."
        ) from exc
    return PyIRI, ml


def iri_vtec_lookup(
    day: date,
    ut_hours: np.ndarray,
    lons_deg: np.ndarray,
    lats_deg: np.ndarray,
    f107: float,
) -> np.ndarray:
    """
    Model VTEC [TECU], shape (len(ut_hours), len(lons_deg)), for one UTC day.

    One PyIRI whole-day evaluation per call (chunked over UT to bound the
    intermediate EDP array); results are cached per (day, F10.7, UT set,
    point set) so repeated renders of the same request are free.
    """
    ut_hours = np.asarray(ut_hours, dtype=float).ravel()
    lons = np.mod(np.asarray(lons_deg, dtype=float).ravel(), 360.0)
    lats = np.asarray(lats_deg, dtype=float).ravel()
    if lons.shape != lats.shape:
        raise ValueError("lons_deg and lats_deg must have the same length.")

    cache_key = (
        day.isoformat(),
        round(float(f107), 1),
        hash(ut_hours.tobytes()),
        hash(lons.tobytes() + lats.tobytes()),
    )
    with _model_cache_lock:
        cached = _model_cache.get(cache_key)
    if cached is not None:
        return cached

    PyIRI, ml = _import_pyiri()
    started = time.monotonic()
    chunks: list[np.ndarray] = []
    for chunk_start in range(0, len(ut_hours), _UT_CHUNK_SIZE):
        ut_chunk = ut_hours[chunk_start : chunk_start + _UT_CHUNK_SIZE]
        _, _, _, _, _, _, edp = ml.IRI_density_1day(
            day.year,
            day.month,
            day.day,
            ut_chunk,
            lons,
            lats,
            IRI_ALT_GRID_KM,
            float(f107),
            PyIRI.coeff_dir,
        )
        chunks.append(np.asarray(ml.edp_to_vtec(edp, IRI_ALT_GRID_KM), dtype=float))
    vtec = np.vstack(chunks) if chunks else np.empty((0, len(lons)))
    logger.info(
        "tec-map iri: %s F10.7=%.1f — %d UTs x %d points in %.1fs",
        day,
        f107,
        len(ut_hours),
        len(lons),
        time.monotonic() - started,
    )

    with _model_cache_lock:
        if len(_model_cache) >= _MODEL_CACHE_MAX_ENTRIES:
            _model_cache.pop(next(iter(_model_cache)))
        _model_cache[cache_key] = vtec
    return vtec


def _ut_hour(ts: pd.Timestamp) -> float:
    return ts.hour + ts.minute / 60.0 + ts.second / 3600.0


def iri_vtec_grid_for_frames(
    frame_times: list[pd.Timestamp],
    grid_lon: np.ndarray,
    grid_lat: np.ndarray,
    f107_override: float | None = None,
) -> tuple[dict[pd.Timestamp, np.ndarray], dict[str, dict[str, float | str]]]:
    """
    IRI VTEC grids per frame: {frame_time: 2-D array shaped like grid_lon}.

    Also returns F10.7 metadata per day: {"YYYY-MM-DD": {"f107": …, "source": …}}.
    """
    grid_lon = np.asarray(grid_lon, dtype=float)
    grid_lat = np.asarray(grid_lat, dtype=float)
    frames = [pd.Timestamp(ts) for ts in frame_times]

    by_day: dict[date, list[pd.Timestamp]] = {}
    for ts in frames:
        by_day.setdefault(ts.date(), []).append(ts)

    grids: dict[pd.Timestamp, np.ndarray] = {}
    f107_meta: dict[str, dict[str, float | str]] = {}
    for day, day_frames in sorted(by_day.items()):
        f107, f107_source = resolve_f107_for_day(day, f107_override)
        f107_meta[day.isoformat()] = {"f107": round(f107, 1), "source": f107_source}
        unique_frames = sorted(set(day_frames))
        ut_hours = np.array([_ut_hour(ts) for ts in unique_frames])
        vtec = iri_vtec_lookup(day, ut_hours, grid_lon.ravel(), grid_lat.ravel(), f107)
        for idx, ts in enumerate(unique_frames):
            grids[ts] = vtec[idx].reshape(grid_lon.shape)
    return grids, f107_meta


def iri_vtec_for_rows(
    frame_times: pd.Series,
    lons_deg: pd.Series,
    lats_deg: pd.Series,
    f107_override: float | None = None,
) -> tuple[np.ndarray, dict[str, dict[str, float | str]]]:
    """
    Row-wise IRI VTEC [TECU] for (frame_time, lon, lat) rows of a series
    export. Rows are grouped per day into one PyIRI evaluation over the
    day's unique frame UTs x the day's row coordinates.
    """
    times = pd.to_datetime(frame_times).reset_index(drop=True)
    lons = np.asarray(lons_deg, dtype=float)
    lats = np.asarray(lats_deg, dtype=float)
    result = np.full(len(times), np.nan)
    f107_meta: dict[str, dict[str, float | str]] = {}

    for day, day_index in times.groupby(times.dt.date).groups.items():
        row_positions = np.asarray(day_index, dtype=int)
        day_times = times.iloc[row_positions]
        unique_frames = sorted(day_times.unique())
        ut_hours = np.array([_ut_hour(pd.Timestamp(ts)) for ts in unique_frames])
        frame_index = {pd.Timestamp(ts): idx for idx, ts in enumerate(unique_frames)}

        f107, f107_source = resolve_f107_for_day(day, f107_override)
        f107_meta[day.isoformat()] = {"f107": round(f107, 1), "source": f107_source}

        vtec = iri_vtec_lookup(day, ut_hours, lons[row_positions], lats[row_positions], f107)
        for local_idx, row_pos in enumerate(row_positions):
            result[row_pos] = vtec[frame_index[pd.Timestamp(day_times.iloc[local_idx])], local_idx]
    return result, f107_meta
