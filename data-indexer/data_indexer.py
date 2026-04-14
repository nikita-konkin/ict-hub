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

import os
import re
import time
import sqlite3
import json
import logging
from pathlib import Path
from typing import TypedDict

# For file watching approach
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False

# Set up logging
logger = logging.getLogger(__name__)

# Minimum seconds between full re-scans
_CACHE_TTL_SEC: float = float(os.getenv('DATA_INDEXER_CACHE_TTL_SEC', '300.0'))

# Persistent cache database path
_CACHE_DB_PATH = os.getenv('DATA_INDEXER_CACHE_DB_PATH', '/app/data/cache.db')

# (path → (file_list_hash, result)) — file list comparison cache
_rinex_cache: dict[str, tuple[str, list]] = {}
_tecsuite_cache: dict[str, tuple[float, list]] = {}
_parquet_cache: dict[str, tuple[float, list]] = {}
_parquet_sat_cache: dict[str, tuple[float, list]] = {}


def _init_cache_db():
    """Initialize the persistent cache database."""
    try:
        os.makedirs(os.path.dirname(_CACHE_DB_PATH), exist_ok=True)
        conn = sqlite3.connect(_CACHE_DB_PATH)
        cursor = conn.cursor()

        # Create cache table if it doesn't exist
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS cache (
                cache_key TEXT PRIMARY KEY,
                cache_type TEXT NOT NULL,
                data TEXT NOT NULL,
                timestamp REAL NOT NULL
            )
        ''')

        # Create index for faster lookups
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_cache_type_timestamp
            ON cache (cache_type, timestamp)
        ''')

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
            ('rinex', _rinex_cache),
            ('tecsuite', _tecsuite_cache),
            ('parquet', _parquet_cache),
            ('parquet_sat', _parquet_sat_cache)
        ]:
            cursor.execute(
                'SELECT cache_key, data, timestamp FROM cache WHERE cache_type = ?',
                (cache_type,)
            )

            for row in cursor.fetchall():
                cache_key, data_json, timestamp = row
                try:
                    data = json.loads(data_json)
                    cache_dict[cache_key] = (timestamp, data)
                except json.JSONDecodeError:
                    continue  # Skip corrupted entries

        conn.close()
        print(f"Loaded {sum(len(c) for c in [_rinex_cache, _tecsuite_cache, _parquet_cache, _parquet_sat_cache])} cache entries from database")

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
        cursor.execute('''
            INSERT OR REPLACE INTO cache (cache_key, cache_type, data, timestamp)
            VALUES (?, ?, ?, ?)
        ''', (cache_key, cache_type, data_json, timestamp))

        conn.commit()
        conn.close()

    except Exception as e:
        print(f"Warning: Failed to save cache to database: {e}")


# Initialize database and load cache on module import
_init_cache_db()
_load_cache_from_db()

def _get_directory_hash(root: Path) -> str:
    """Generate a hash of all files in directory tree for change detection."""
    import hashlib
    file_paths = []
    try:
        for path in root.rglob('*'):
            if path.is_file():
                file_paths.append(str(path.relative_to(root)))
        file_paths.sort()
        return hashlib.md5('\n'.join(file_paths).encode()).hexdigest()
    except OSError:
        return ""

# File watching observers (path → observer)
if WATCHDOG_AVAILABLE:
    _observers: dict[str, Observer] = {}
else:
    _observers: dict[str, object] = {}
# Cache invalidation flags (path → bool)
_cache_invalidated: dict[str, bool] = {}

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


def _day_sort_key(name: str) -> tuple[int, int, str]:
    """Sort days numerically. For MM/DD format, sort by month then day. For DOY, sort numerically."""
    if '/' in name:
        month, day = name.split('/')
        return (int(month), int(day), name)
    else:
        return (int(name), len(name), name)


def _year_sort_key(name: str) -> int:
    """Sort years numerically by their 4-digit prefix."""
    return int(name[:4])


def _abstec_day_sort_key(name: str) -> tuple[int, int, str]:
    """Sort AbsTEC day folders numerically while preserving original zero-padding."""
    return (int(name), len(name), name)


def list_rinex_server_structure(host_root: str) -> list[YearInfo]:
    """
    Return discovered RINEX server structure under host_root.

    Results are cached and reused as long as the directory file list hasn't
    changed (detects added/removed files regardless of mtime issues).

        Supported layouts:
            <root>/YYYY_original/DOY/<station>.zip        (DOY can be 2 or 3 digits)
            <root>/YYYY_original/MM/DD/<station>.zip
    """
    if not host_root:
        return []
    root = Path(host_root)
    if not root.exists() or not root.is_dir():
        return []

    current_hash = _get_directory_hash(root)
    cached_hash, cached_result = _rinex_cache.get(host_root, (None, None))
    if current_hash and current_hash == cached_hash:
        return cached_result  # type: ignore[return-value]

    logger.info(f"Starting RINEX indexing for path: {host_root}")
    result = _scan_rinex(root)
    logger.info(f"Completed RINEX indexing for path: {host_root} - found {len(result)} years")
    _rinex_cache[host_root] = (current_hash, result)
    _save_cache_to_db('rinex', host_root, (current_hash, result))
    return result


def _scan_rinex(root: Path) -> list[YearInfo]:
    """Full filesystem scan — called only when cache is cold or stale."""
    years: list[YearInfo] = []
    for year_dir in root.iterdir():
        if not year_dir.is_dir():
            continue
        if not YEAR_DIR_RE.fullmatch(year_dir.name):
            continue

        days: list[DayInfo] = []

        for top_dir in sorted(year_dir.iterdir(), key=lambda d: d.name):
            if not top_dir.is_dir():
                continue
            if not DAY_DIR_RE.fullmatch(top_dir.name):
                continue

            # Layout A: YYYY_original/DOY/<station>.zip (DOY can be 2 or 3 digits)
            direct_zips = sum(
                1
                for entry in top_dir.iterdir()
                if entry.is_file() and entry.suffix.lower() == ".zip"
            )
            if direct_zips:
                days.append({"day": top_dir.name, "stations": direct_zips})
                continue

            # Layout B: YYYY_original/MM/DD/<station>.zip
            month_num = int(top_dir.name)
            if not 1 <= month_num <= 12:
                continue

            for day_dir in sorted(top_dir.iterdir(), key=lambda d: d.name):
                if not day_dir.is_dir():
                    continue
                if not DAY_IN_MONTH_RE.fullmatch(day_dir.name):
                    continue
                day_num = int(day_dir.name)
                if not 1 <= day_num <= 31:
                    continue

                stations = sum(
                    1
                    for entry in day_dir.iterdir()
                    if entry.is_file() and entry.suffix.lower() == ".zip"
                )
                days.append({"day": f"{top_dir.name}/{day_dir.name}", "stations": stations})

        days.sort(key=lambda item: _day_sort_key(item["day"]))
        years.append({"year": year_dir.name, "days": days})

    years.sort(key=lambda item: _year_sort_key(str(item["year"])), reverse=True)
    return years


def list_tecsuite_output_structure(host_root: str) -> list[AbsTecYearInfo]:
    """
    Return TEC-suite DAT output structure for AbsTEC selection UI.

    Results are cached and reused for CACHE_TTL_SEC seconds (configurable via
    DATA_INDEXER_CACHE_TTL_SEC environment variable) to balance freshness with performance.

    Expected layouts:
      <root>/YYYY/DDD/SITE/*.dat
      <root>/in/YYYY/DDD/SITE/*.dat
    """
    if not host_root:
        return []
    root = Path(host_root)
    if not root.exists() or not root.is_dir():
        return []

    now = time.monotonic()
    cached_time, cached_result = _tecsuite_cache.get(host_root, (None, None))
    if cached_time is not None and now - cached_time < _CACHE_TTL_SEC:
        return cached_result  # type: ignore[return-value]

    logger.info(f"Starting TEC-suite indexing for path: {host_root}")
    scan_root = root / "in" if (root / "in").is_dir() else root
    result = _scan_tecsuite(scan_root)
    logger.info(f"Completed TEC-suite indexing for path: {host_root} - found {len(result)} years")
    _tecsuite_cache[host_root] = (now, result)
    _save_cache_to_db('tecsuite', host_root, (now, result))
    return result


def list_parquet_output_structure(host_root: str) -> list[dict[str, object]]:
    """
    Return parquet output structure under host_root for the year/day UI.

    Results are cached and reused for CACHE_TTL_SEC seconds (configurable via
    DATA_INDEXER_CACHE_TTL_SEC environment variable) to balance freshness with performance.

    Expected layout (mirrors the DAT source root):
      <root>/YYYY/DDD/…   (any files/subdirs below DDD are ignored)
    """
    if not host_root:
        return []
    root = Path(host_root)
    if not root.exists() or not root.is_dir():
        return []

    now = time.monotonic()
    cached_time, cached_result = _parquet_cache.get(host_root, (None, None))
    if cached_time is not None and now - cached_time < _CACHE_TTL_SEC:
        return cached_result  # type: ignore[return-value]

    logger.info(f"Starting Parquet indexing for path: {host_root}")
    result = _scan_parquet(root)
    logger.info(f"Completed Parquet indexing for path: {host_root} - found {len(result)} years")
    _parquet_cache[host_root] = (now, result)
    _save_cache_to_db('parquet', host_root, (now, result))
    return result


def list_parquet_satellite_structure(host_root: str) -> list[dict[str, object]]:
    """
    Return parquet structure with stations and satellites under host_root.

    Results are cached and reused for CACHE_TTL_SEC seconds (configurable via
    DATA_INDEXER_CACHE_TTL_SEC environment variable) to balance freshness with performance.

    Expected layout (best effort):
      <root>/YYYY/DDD/SITE/*.parquet
      <root>/YYYY/DDD/**/*.parquet
    """
    if not host_root:
        return []
    root = Path(host_root)
    if not root.exists() or not root.is_dir():
        return []

    now = time.monotonic()
    cached_time, cached_result = _parquet_sat_cache.get(host_root, (None, None))
    if cached_time is not None and now - cached_time < _CACHE_TTL_SEC:
        return cached_result  # type: ignore[return-value]

    logger.info(f"Starting Parquet satellite indexing for path: {host_root}")
    result = _scan_parquet_satellites(root)
    logger.info(f"Completed Parquet satellite indexing for path: {host_root} - found {len(result)} years")
    _parquet_sat_cache[host_root] = (now, result)
    _save_cache_to_db('parquet_sat', host_root, (now, result))
    return result


def _scan_parquet(root: Path) -> list[dict[str, object]]:
    """Full filesystem scan for parquet output roots."""
    years: list[dict[str, object]] = []

    for year_dir in root.iterdir():
        if not year_dir.is_dir() or not ABSTEC_YEAR_DIR_RE.fullmatch(year_dir.name):
            continue

        days: list[str] = []
        for day_dir in year_dir.iterdir():
            if not day_dir.is_dir() or not ABSTEC_DAY_DIR_RE.fullmatch(day_dir.name):
                continue
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

        days: list[dict[str, object]] = []
        for day_dir in year_dir.iterdir():
            if not day_dir.is_dir() or not ABSTEC_DAY_DIR_RE.fullmatch(day_dir.name):
                continue

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
                # Sample the alphabetically first station dir for satellite IDs.
                # Satellite sets are uniform across stations on the same day.
                sample_dir = day_dir / min(stations)
                for pq_file in sample_dir.glob("*.parquet"):
                    stem = pq_file.stem.upper()
                    for match in SATELLITE_RE.findall(stem):
                        satellites.add(match)
            else:
                # Flat layout: parquet files live directly under day_dir.
                for pq_file in flat_pq:
                    stem = pq_file.stem.upper()
                    for match in SATELLITE_RE.findall(stem):
                        satellites.add(match)

            if not stations and not satellites:
                continue

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

        days: list[AbsTecDayInfo] = []
        for day_dir in year_dir.iterdir():
            if not day_dir.is_dir() or not ABSTEC_DAY_DIR_RE.fullmatch(day_dir.name):
                continue

            sites: list[str] = []
            # Layout A: YYYY/DDD/SITE_DIR/*.dat  (site as subdirectory)
            for site_dir in day_dir.iterdir():
                if not site_dir.is_dir():
                    continue
                has_dat = any(
                    entry.is_file() and entry.suffix.lower() == ".dat"
                    for entry in site_dir.rglob("*")
                )
                if has_dat:
                    sites.append(site_dir.name)

            # Layout B: YYYY/DDD/SITE.dat  (flat – site name = file stem)
            if not sites:
                sites = [
                    entry.stem
                    for entry in day_dir.iterdir()
                    if entry.is_file() and entry.suffix.lower() == ".dat"
                ]

            if sites:
                sites.sort()
                days.append({"day": day_dir.name.zfill(3), "sites": sites})

        days.sort(key=lambda item: _abstec_day_sort_key(item["day"]))
        if days:
            years.append({"year": year_dir.name, "days": days})

    years.sort(key=lambda item: int(item["year"]), reverse=True)
    return years