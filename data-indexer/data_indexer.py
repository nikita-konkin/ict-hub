"""
data_indexer.py — data indexing service for RINEX, TEC-suite, and AbsTEC data.

Provides REST endpoints that return XML structures for:
- RINEX server structure
- TEC-suite DAT output structure (in/out)
- AbsTEC output structure (in/out)
- Parquet output structure

All endpoints return XML responses for consumption by other services.

Configuration:
- DATA_INDEXER_CACHE_TTL_SEC: Cache TTL in seconds (default: 300.0 = 5 minutes)
- DATA_INDEXER_CACHE_DB_PATH: Path to persistent cache database (default: /app/data/cache.db)
"""

import json
import logging
import os
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from watchdog.observers.api import BaseObserver

# For file watching approach
try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False

# Set up logging
logger = logging.getLogger(__name__)

# Logging will be configured by the main app module
# Test logging at module load
logger.info("[MODULE] data_indexer module loaded")
logger.debug("[MODULE] Debug logging test")

# Minimum seconds between full re-scans
_CACHE_TTL_SEC: float = float(os.getenv("DATA_INDEXER_CACHE_TTL_SEC", "300.0"))

# Persistent cache database path
_CACHE_DB_PATH = os.getenv("DATA_INDEXER_CACHE_DB_PATH", "/app/data/cache.db")

# path → (wall-clock timestamp, result). Timestamps are persisted to the cache
# database, so they must stay meaningful across restarts and host reboots.
_rinex_cache: dict[str, tuple[float, list]] = {}
# (cache_type, path) pairs with a background refresh running
_refresh_in_progress: set[tuple[str, str]] = set()
_refresh_lock = threading.Lock()
_tecsuite_cache: dict[str, tuple[float, list]] = {}
_parquet_cache: dict[str, tuple[float, list]] = {}
_parquet_sat_cache: dict[str, tuple[float, list]] = {}
_watch_lock = threading.Lock()


def _init_cache_db():
    """Initialize the persistent cache database."""
    try:
        os.makedirs(os.path.dirname(_CACHE_DB_PATH), exist_ok=True)
        conn = sqlite3.connect(_CACHE_DB_PATH)
        cursor = conn.cursor()

        # Create cache table if it doesn't exist
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cache (
                cache_key TEXT PRIMARY KEY,
                cache_type TEXT NOT NULL,
                data TEXT NOT NULL,
                timestamp REAL NOT NULL
            )
        """)

        # Create index for faster lookups
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_cache_type_timestamp
            ON cache (cache_type, timestamp)
        """)

        conn.commit()
        conn.close()
    except Exception as e:
        # If database initialization fails, continue without persistence
        print(f"Warning: Failed to initialize cache database: {e}")


def _load_cache_from_db():
    """Load cached data from database on startup."""
    try:
        if not os.path.exists(_CACHE_DB_PATH):
            return

        conn = sqlite3.connect(_CACHE_DB_PATH)
        cursor = conn.cursor()

        # Load each cache type
        for cache_type, cache_dict in [
            ("rinex", _rinex_cache),
            ("tecsuite", _tecsuite_cache),
            ("parquet", _parquet_cache),
            ("parquet_sat", _parquet_sat_cache),
        ]:
            cursor.execute("SELECT cache_key, data, timestamp FROM cache WHERE cache_type = ?", (cache_type,))

            for row in cursor.fetchall():
                cache_key, data_json, timestamp = row
                try:
                    data = json.loads(data_json)
                    cache_dict[cache_key] = (timestamp, data)
                except json.JSONDecodeError:
                    continue  # Skip corrupted entries

        conn.close()
        entries = sum(len(c) for c in [_rinex_cache, _tecsuite_cache, _parquet_cache, _parquet_sat_cache])
        print(f"Loaded {entries} cache entries from database")

    except Exception as e:
        print(f"Warning: Failed to load cache from database: {e}")


def _save_cache_to_db(cache_type: str, cache_key: str, data: tuple):
    """Save cache entry to database."""
    try:
        conn = sqlite3.connect(_CACHE_DB_PATH)
        cursor = conn.cursor()

        timestamp, result = data
        data_json = json.dumps(result)

        # Insert or replace cache entry
        cursor.execute(
            """
            INSERT OR REPLACE INTO cache (cache_key, cache_type, data, timestamp)
            VALUES (?, ?, ?, ?)
        """,
            (cache_key, cache_type, data_json, timestamp),
        )

        conn.commit()
        conn.close()

    except Exception as e:
        print(f"Warning: Failed to save cache to database: {e}")


# Initialize database and load cache on module import
_init_cache_db()
_load_cache_from_db()


# File watching observers (path → observer, or None if it could not start)
_observers: dict[str, "BaseObserver | None"] = {}

# Filesystem change tracking. The watcher bumps a root's generation on every
# structural change, and each (cache type, root) remembers the generation its
# data was scanned at. A cache is invalidated while its generation lags behind,
# so one change refreshes every cache type built from that root (e.g. both
# /parquet and /parquet-satellites), and a change that lands during a scan
# triggers another scan instead of being lost.
_root_generation: dict[str, int] = {}
_scanned_generation: dict[tuple[str, str], int] = {}

# Only these change what the indexer reports; opens, closes and in-place writes
# (a converter filling its output files) would otherwise force constant rescans.
_STRUCTURAL_EVENTS = {"created", "deleted", "moved"}

YEAR_DIR_RE = re.compile(r"^\d{4}_original$")
DAY_DIR_RE = re.compile(r"^\d{2,3}$")
MONTH_DIR_RE = re.compile(r"^\d{2}$")
DAY_IN_MONTH_RE = re.compile(r"^\d{2}$")
ABSTEC_YEAR_DIR_RE = re.compile(r"^\d{4}$")
ABSTEC_DAY_DIR_RE = re.compile(r"^\d{1,3}$")
SATELLITE_RE = re.compile(r"(?<![A-Z0-9])([A-Z][0-9]{2})(?![0-9])")


class DayInfo(TypedDict):
    day: str
    stations: int


class YearInfo(TypedDict):
    year: str
    days: list[DayInfo]


class AbsTecDayInfo(TypedDict):
    day: str
    sites: list[str]


class AbsTecYearInfo(TypedDict):
    year: str
    days: list[AbsTecDayInfo]


if WATCHDOG_AVAILABLE:

    class _RootChangeHandler(FileSystemEventHandler):
        """Invalidate a watched root when filesystem events arrive."""

        def __init__(self, host_root: str):
            self.host_root = host_root

        def on_any_event(self, event):
            if event.event_type not in _STRUCTURAL_EVENTS:
                return
            _root_generation[self.host_root] = _root_generation.get(self.host_root, 0) + 1
            logger.debug(
                "[WATCHER] Change detected for %s via %s on %s",
                self.host_root,
                event.event_type,
                event.src_path,
            )


def _ensure_watcher(host_root: str, root: Path) -> None:
    """Start a watchdog observer for a root if watchdog is available."""
    if not WATCHDOG_AVAILABLE:
        return

    with _watch_lock:
        if host_root in _observers:
            return

        try:
            observer = Observer()
            observer.schedule(_RootChangeHandler(host_root), str(root), recursive=True)
            observer.start()
        except Exception as exc:
            # e.g. inotify watch limit reached, or a mount that does not support it.
            # Caching still works; changes are just picked up by the TTL instead.
            logger.warning("[WATCHER] Cannot watch %s, falling back to TTL refresh: %s", host_root, exc)
            _observers[host_root] = None
            return
        _observers[host_root] = observer
        logger.info("[WATCHER] Started observer for %s", host_root)


def stop_all_watchers() -> None:
    """Stop all filesystem observers during service shutdown."""
    if not WATCHDOG_AVAILABLE:
        return

    with _watch_lock:
        observers = list(_observers.items())
        _observers.clear()

    for host_root, observer in observers:
        if observer is None:
            continue
        try:
            observer.stop()
            observer.join(timeout=5)
            logger.info("[WATCHER] Stopped observer for %s", host_root)
        except Exception as exc:
            logger.warning("[WATCHER] Failed to stop observer for %s: %s", host_root, exc)


def _cache_is_invalidated(cache_type: str, host_root: str) -> bool:
    return _scanned_generation.get((cache_type, host_root), 0) < _root_generation.get(host_root, 0)


def _scan_and_store(
    cache_type: str,
    host_root: str,
    root: Path,
    scan_fn,
    cache_dict: dict,
):
    """Scan root, then store the result in memory and in the cache database."""
    # Read the generation before scanning, so changes made during the scan
    # leave this cache marked as invalidated.
    generation = _root_generation.get(host_root, 0)
    result = scan_fn(root)
    ts = time.time()
    cache_dict[host_root] = (ts, result)
    _scanned_generation[(cache_type, host_root)] = generation
    _save_cache_to_db(cache_type, host_root, (ts, result))
    return result


def _refresh_invalidated_cache(
    cache_type: str,
    host_root: str,
    root: Path,
    scan_fn,
    cache_dict: dict,
):
    """Refresh a cache synchronously after a filesystem event invalidates it."""
    if not _cache_is_invalidated(cache_type, host_root):
        return None

    logger.info("[%s] Cache invalidated for %s - rescanning now", _LOG_TAGS[cache_type], host_root)
    return _scan_and_store(cache_type, host_root, root, scan_fn, cache_dict)


def _day_sort_key(name: str) -> tuple[int, int, str]:
    """Sort days numerically. For MM/DD format, sort by month then day. For DOY, sort numerically."""
    if "/" in name:
        month, day = name.split("/")
        return (int(month), int(day), name)
    else:
        return (int(name), len(name), name)


def _year_sort_key(name: str) -> int:
    """Sort years numerically by their 4-digit prefix."""
    return int(name[:4])


def _abstec_day_sort_key(name: str) -> tuple[int, int, str]:
    """Sort AbsTEC day folders numerically while preserving original zero-padding."""
    return (int(name), len(name), name)


# Log tag per cache type, as documented in README.md.
_LOG_TAGS = {
    "rinex": "RINEX",
    "tecsuite": "TEC-SUITE",
    "parquet": "PARQUET",
    "parquet_sat": "PARQUET-SAT",
}


def _cached_listing(
    cache_type: str,
    host_root: str,
    scan_fn,
    cache_dict: dict,
    scan_subdir: str | None = None,
):
    """
    Serve a directory listing from cache, stale-while-revalidate:
      - the watcher saw a change      → rescan now
      - younger than the TTL          → cached result
      - older than the TTL            → cached result now, rescan in the background
      - never scanned                 → scan now

    scan_subdir: scan <root>/<scan_subdir> instead of <root> when it exists.
    """
    tag = _LOG_TAGS[cache_type]
    if not host_root:
        return []
    root = Path(host_root)
    if not root.exists() or not root.is_dir():
        logger.debug("[%s] Root path does not exist or is not a directory: %s", tag, host_root)
        return []

    _ensure_watcher(host_root, root)
    scan_root = root
    if scan_subdir and (root / scan_subdir).is_dir():
        scan_root = root / scan_subdir

    invalidated_result = _refresh_invalidated_cache(cache_type, host_root, scan_root, scan_fn, cache_dict)
    if invalidated_result is not None:
        return invalidated_result

    cached_time, cached_result = cache_dict.get(host_root, (None, None))
    if cached_time is None:
        logger.info("[%s] Cold start scan for %s", tag, host_root)
        return _scan_and_store(cache_type, host_root, scan_root, scan_fn, cache_dict)

    cache_age = time.time() - cached_time
    if 0 <= cache_age < _CACHE_TTL_SEC:
        logger.debug("[%s] Cache HIT (age: %.1fs, TTL: %ss)", tag, cache_age, _CACHE_TTL_SEC)
        return cached_result

    logger.info("[%s] Cache STALE (age: %.1fs) — serving old result, refreshing in background", tag, cache_age)
    _trigger_background_refresh(cache_type, host_root, scan_root, scan_fn, cache_dict)
    return cached_result


def list_rinex_server_structure(host_root: str) -> list[YearInfo]:
    """
    Return the RINEX server structure under host_root.

    Supported layouts:
      <root>/YYYY_original/DDD/*.zip       (day of year)
      <root>/YYYY_original/MM/DD/*.zip     (month/day, used from 2019)
    """
    return _cached_listing("rinex", host_root, _scan_rinex, _rinex_cache)


def list_tecsuite_output_structure(host_root: str) -> list[AbsTecYearInfo]:
    """
    Return TEC-suite DAT output structure for the AbsTEC selection UI.

    Expected layouts:
      <root>/YYYY/DDD/SITE/*.dat
      <root>/in/YYYY/DDD/SITE/*.dat
    """
    return _cached_listing("tecsuite", host_root, _scan_tecsuite, _tecsuite_cache, scan_subdir="in")


def list_parquet_output_structure(host_root: str) -> list[dict[str, object]]:
    """
    Return parquet output structure under host_root for the year/day UI.

    Expected layout (mirrors the DAT source root):
      <root>/YYYY/DDD/…   (any files/subdirs below DDD are ignored)
    """
    return _cached_listing("parquet", host_root, _scan_parquet, _parquet_cache)


def list_parquet_satellite_structure(host_root: str) -> list[dict[str, object]]:
    """
    Return parquet structure with stations and satellites under host_root.

    Expected layouts (best effort):
      <root>/YYYY/DDD/SITE/*.parquet
      <root>/YYYY/DDD/*.parquet
    """
    return _cached_listing("parquet_sat", host_root, _scan_parquet_satellites, _parquet_sat_cache)


def _trigger_background_refresh(
    cache_type: str,
    host_root: str,
    root: Path,
    scan_fn,
    cache_dict: dict,
) -> None:
    """Kick off a background thread to refresh any cache without blocking the caller."""
    key = (cache_type, host_root)
    with _refresh_lock:
        if key in _refresh_in_progress:
            logger.debug("[%s] Refresh already in progress for %s, skipping", _LOG_TAGS[cache_type], host_root)
            return
        _refresh_in_progress.add(key)

    tag = _LOG_TAGS[cache_type]

    def _do_refresh():
        try:
            logger.info("[%s] Background refresh started for %s", tag, host_root)
            result = _scan_and_store(cache_type, host_root, root, scan_fn, cache_dict)
            logger.info("[%s] Background refresh complete — %d entries", tag, len(result))
        except Exception as e:
            logger.error("[%s] Background refresh failed: %s", tag, e)
        finally:
            with _refresh_lock:
                _refresh_in_progress.discard(key)

    threading.Thread(target=_do_refresh, daemon=True).start()


def _scan_rinex(root: Path) -> list[YearInfo]:
    # Step 1: collect year directories
    with os.scandir(root) as it:
        year_entries = [e for e in it if e.is_dir() and YEAR_DIR_RE.fullmatch(e.name)]

    # Step 2: each worker handles exactly ONE year_entry
    def scan_year(year_entry) -> YearInfo | None:
        days: list[DayInfo] = []

        with os.scandir(year_entry.path) as it:
            top_entries = sorted([e for e in it if e.is_dir() and DAY_DIR_RE.fullmatch(e.name)], key=lambda e: e.name)

        for top_entry in top_entries:
            with os.scandir(top_entry.path) as it:
                entries = list(it)

            direct_zips = sum(1 for e in entries if e.is_file() and e.name.lower().endswith(".zip"))
            if direct_zips:
                days.append({"day": top_entry.name, "stations": direct_zips})
                continue

            # Layout B: MM/DD
            try:
                month_num = int(top_entry.name)
            except ValueError:
                continue
            if not 1 <= month_num <= 12:
                continue

            for day_entry in sorted([e for e in entries if e.is_dir()], key=lambda e: e.name):
                if not DAY_IN_MONTH_RE.fullmatch(day_entry.name):
                    continue
                with os.scandir(day_entry.path) as it2:
                    stations = sum(1 for e in it2 if e.is_file() and e.name.lower().endswith(".zip"))
                days.append({"day": f"{top_entry.name}/{day_entry.name}", "stations": stations})

        if not days:
            return None

        days.sort(key=lambda item: _day_sort_key(item["day"]))
        return {"year": year_entry.name, "days": days}  # ← uses its OWN year_entry

    # Step 3: run all year scans in parallel
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(scan_year, year_entries))

    years = [r for r in results if r is not None]
    years.sort(key=lambda item: _year_sort_key(str(item["year"])), reverse=True)
    return years


def _scan_parquet(root: Path) -> list[dict[str, object]]:
    """Full filesystem scan for parquet output roots."""
    years: list[dict[str, object]] = []

    for year_dir in root.iterdir():
        if not year_dir.is_dir() or not ABSTEC_YEAR_DIR_RE.fullmatch(year_dir.name):
            continue

        logger.debug("[PARQUET] Scanning year directory: %s", year_dir)
        days: list[str] = []
        for day_dir in year_dir.iterdir():
            if not day_dir.is_dir() or not ABSTEC_DAY_DIR_RE.fullmatch(day_dir.name):
                continue
            logger.debug("[PARQUET] Scanning day directory: %s", day_dir)
            days.append(day_dir.name.zfill(3))

        if days:
            days.sort(key=lambda d: _abstec_day_sort_key(d))
            years.append({"year": year_dir.name, "days": days})

    years.sort(key=lambda item: int(str(item["year"])), reverse=True)
    return years


def _scan_parquet_satellites(root: Path) -> list[dict[str, object]]:
    """Full filesystem scan for parquet roots with station/satellite extraction."""
    years: list[dict[str, object]] = []

    for year_dir in root.iterdir():
        if not year_dir.is_dir() or not ABSTEC_YEAR_DIR_RE.fullmatch(year_dir.name):
            continue

        logger.debug("[PARQUET-SAT] Scanning year directory: %s", year_dir)
        days: list[dict[str, object]] = []
        for day_dir in year_dir.iterdir():
            if not day_dir.is_dir() or not ABSTEC_DAY_DIR_RE.fullmatch(day_dir.name):
                continue

            logger.debug("[PARQUET-SAT] Scanning day directory: %s", day_dir)
            stations: set[str] = set()
            satellites: set[str] = set()

            # Stations = immediate subdirectories of day_dir.
            # Satellites are extracted from ONE representative station's parquet
            # file names only — avoids scanning millions of files across all
            # stations on large datasets (significant speedup: O(stations) vs
            # O(stations × files_per_station) for the full rglob approach).
            flat_pq: list[Path] = []
            for entry in day_dir.iterdir():
                if entry.is_dir():
                    stations.add(entry.name)
                elif entry.suffix.lower() == ".parquet":
                    flat_pq.append(entry)

            if stations:
                logger.debug(
                    "[PARQUET-SAT] Found %s stations: %s%s",
                    len(stations),
                    sorted(stations)[:5],
                    "..." if len(stations) > 5 else "",
                )
                # Sample the alphabetically first station dir for satellite IDs.
                # Satellite sets are uniform across stations on the same day.
                sample_dir = day_dir / min(stations)
                logger.debug("[PARQUET-SAT] Sampling satellites from: %s", sample_dir)
                for pq_file in sample_dir.glob("*.parquet"):
                    stem = pq_file.stem.upper()
                    for match in SATELLITE_RE.findall(stem):
                        satellites.add(match)
            else:
                logger.debug("[PARQUET-SAT] Using flat layout with %s parquet files", len(flat_pq))
                # Flat layout: parquet files live directly under day_dir.
                for pq_file in flat_pq:
                    stem = pq_file.stem.upper()
                    for match in SATELLITE_RE.findall(stem):
                        satellites.add(match)

            if not stations and not satellites:
                continue

            logger.debug(
                "[PARQUET-SAT] Day %s has %s stations, %s satellites", day_dir.name, len(stations), len(satellites)
            )
            days.append(
                {
                    "day": day_dir.name.zfill(3),
                    "stations": sorted(stations),
                    "satellites": sorted(satellites),
                }
            )

        if days:
            days.sort(key=lambda item: _abstec_day_sort_key(str(item["day"])))
            years.append({"year": year_dir.name, "days": days})

    years.sort(key=lambda item: int(str(item["year"])), reverse=True)
    return years


def _scan_tecsuite(scan_root: Path) -> list[AbsTecYearInfo]:
    """Full filesystem scan — called only when cache is cold or stale."""
    years: list[AbsTecYearInfo] = []

    for year_dir in scan_root.iterdir():
        if not year_dir.is_dir() or not ABSTEC_YEAR_DIR_RE.fullmatch(year_dir.name):
            continue

        logger.debug("[TEC-SUITE] Scanning year directory: %s", year_dir)
        days: list[AbsTecDayInfo] = []
        for day_dir in year_dir.iterdir():
            if not day_dir.is_dir() or not ABSTEC_DAY_DIR_RE.fullmatch(day_dir.name):
                continue

            logger.debug("[TEC-SUITE] Scanning day directory: %s", day_dir)
            sites: list[str] = []
            # Layout A: YYYY/DDD/SITE_DIR/*.dat  (site as subdirectory)
            for site_dir in day_dir.iterdir():
                if not site_dir.is_dir():
                    continue
                # has_dat = any(
                #     entry.is_file() and entry.suffix.lower() == ".dat"
                #     for entry in site_dir.rglob("*")
                # )
                with os.scandir(site_dir) as site_entries:
                    has_dat = any(e.is_file() and e.name.lower().endswith(".dat") for e in site_entries)
                if has_dat:
                    logger.debug("[TEC-SUITE] Found site with .dat files: %s", site_dir.name)
                    sites.append(site_dir.name)

            # Layout B: YYYY/DDD/SITE.dat  (flat – site name = file stem)
            if not sites:
                sites = [
                    entry.stem for entry in day_dir.iterdir() if entry.is_file() and entry.suffix.lower() == ".dat"
                ]
                if sites:
                    logger.debug("[TEC-SUITE] Found flat layout .dat files: %s", sites)

            if sites:
                sites.sort()
                days.append({"day": day_dir.name.zfill(3), "sites": sites})

        days.sort(key=lambda item: _abstec_day_sort_key(item["day"]))
        if days:
            years.append({"year": year_dir.name, "days": days})

    years.sort(key=lambda item: int(item["year"]), reverse=True)
    return years


def warm_up_caches(paths: dict[str, str]) -> None:
    """Scan every configured root once so the first requests hit a warm cache."""
    for name, fn, path in (
        ("RINEX", list_rinex_server_structure, paths.get("rinex")),
        ("TEC-suite", list_tecsuite_output_structure, paths.get("tecsuite")),
        ("TEC-suite parquet", list_parquet_output_structure, paths.get("parquet_tecsuite")),
        ("TEC-suite parquet satellites", list_parquet_satellite_structure, paths.get("parquet_tecsuite")),
        ("AbsTEC parquet", list_parquet_output_structure, paths.get("parquet_abstec")),
        ("AbsTEC parquet satellites", list_parquet_satellite_structure, paths.get("parquet_abstec")),
    ):
        if not path:
            continue
        try:
            started = time.time()
            fn(path)
            logger.info("[WARM-UP] %s indexed in %.1fs (%s)", name, time.time() - started, path)
        except Exception as exc:
            # One unreadable root must not stop the others from warming up.
            logger.warning("[WARM-UP] %s indexing failed for %s: %s", name, path, exc)
