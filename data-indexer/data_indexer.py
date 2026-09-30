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
- DATA_INDEXER_MIN_REINDEX_INTERVAL_SEC: Minimum wall-clock age of the previous
  full index before startup indexing runs again (default: 86400 = 1 day).
  Set 0 to always index on startup.
"""

from concurrent.futures import ThreadPoolExecutor
import os
import re
import time
import sqlite3
import json
import logging
from pathlib import Path
from typing import TypedDict

import threading
import errno
# For file watching approach
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False

_WATCHERS_ENABLED: bool = os.getenv("DATA_INDEXER_WATCHERS_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}
_MAX_YEARS: int = int(os.getenv("DATA_INDEXER_MAX_YEARS", "0") or "0")

# Set up logging
logger = logging.getLogger(__name__)

# Logging will be configured by the main app module
# Test logging at module load
logger.info("[MODULE] data_indexer module loaded")
logger.debug("[MODULE] Debug logging test")

# Minimum seconds between full re-scans
_CACHE_TTL_SEC: float = float(os.getenv('DATA_INDEXER_CACHE_TTL_SEC', '300.0'))

# Max workers used during filesystem scans (I/O bound).
def _scan_workers() -> int:
    raw = os.getenv("DATA_INDEXER_SCAN_WORKERS", "").strip()
    if not raw:
        cpu = os.cpu_count() or 4
        return max(1, min(8, cpu))
    try:
        value = int(raw)
    except ValueError:
        cpu = os.cpu_count() or 4
        return max(1, min(8, cpu))
    return max(1, min(32, value))

# Persistent cache database path
_CACHE_DB_PATH = os.getenv('DATA_INDEXER_CACHE_DB_PATH', '/app/data/cache.db')

# How stale the previous full index must be before startup indexing runs again.
# Restarting the service is cheap; re-walking the whole RINEX tree is not, so a
# restart shortly after a successful index should not trigger another scan.
_MIN_REINDEX_INTERVAL_SEC: float = float(
    os.getenv('DATA_INDEXER_MIN_REINDEX_INTERVAL_SEC', '86400')
)

# Key used in the meta table for the last completed full index (wall clock).
_LAST_FULL_INDEX_KEY = 'last_full_index_at'

# 2001-09-09. Cache timestamps are wall clock; anything below this was written
# by an older build using time.monotonic() and must not be treated as a date.
_MIN_PLAUSIBLE_EPOCH = 1_000_000_000.0

# (path → (file_list_hash, result)) — file list comparison cache
# _rinex_cache: dict[str, tuple[str, list]] = {}
_rinex_cache: dict[str, tuple[float, list]] = {}
# (cache type, root) pairs with a background refresh running.
_refresh_in_progress: set[tuple[str, str]] = set()
_refresh_lock = threading.Lock()
_tecsuite_cache: dict[str, tuple[float, list]] = {}
_abstec_cache: dict[str, tuple[float, list]] = {}
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

        # Service-level markers (e.g. when the last full index completed).
        # Kept separate from the per-path `cache` table because it records a
        # property of the service, not of any one indexed root.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value REAL NOT NULL
            )
        ''')

        conn.commit()
        conn.close()
    except Exception as e:
        # If database initialization fails, continue without persistence
        print(f"Warning: Failed to initialize cache database: {e}")


def get_last_full_index_time() -> float | None:
    """Return the wall-clock time of the last completed full index, if known."""
    try:
        conn = sqlite3.connect(_CACHE_DB_PATH)
        cursor = conn.cursor()
        cursor.execute('SELECT value FROM meta WHERE key = ?', (_LAST_FULL_INDEX_KEY,))
        row = cursor.fetchone()
        conn.close()
    except Exception as e:
        print(f"Warning: Failed to read last index time: {e}")
        return None
    if not row:
        return None
    try:
        return float(row[0])
    except (TypeError, ValueError):
        return None


def set_last_full_index_time(timestamp: float | None = None) -> None:
    """Record that a full index just completed (wall clock)."""
    value = time.time() if timestamp is None else float(timestamp)
    try:
        conn = sqlite3.connect(_CACHE_DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            'INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)',
            (_LAST_FULL_INDEX_KEY, value),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Warning: Failed to record last index time: {e}")


def should_run_full_index(now: float | None = None) -> tuple[bool, str, float | None]:
    """Decide whether a startup full index should run.

    Indexing is refused while the previous index is younger than
    DATA_INDEXER_MIN_REINDEX_INTERVAL_SEC, so restarting the service does not
    re-walk the whole RINEX tree. Returns (should_run, reason, age_seconds).
    """
    if _MIN_REINDEX_INTERVAL_SEC <= 0:
        return True, "minimum re-index interval is disabled", None

    last = get_last_full_index_time()
    if last is None:
        return True, "no previous index recorded", None

    current = time.time() if now is None else now
    age = current - last

    if age < 0:
        # Clock moved backwards (host time sync); treat the marker as unusable
        # rather than blocking indexing forever.
        return True, "recorded index time is in the future", age

    if age < _MIN_REINDEX_INTERVAL_SEC:
        return (
            False,
            f"last index was {age / 3600:.1f}h ago, below the "
            f"{_MIN_REINDEX_INTERVAL_SEC / 3600:.1f}h minimum re-index interval",
            age,
        )

    return True, f"last index was {age / 3600:.1f}h ago", age


def _is_epoch_timestamp(value) -> bool:
    """True when a stored timestamp looks like wall-clock (seconds since epoch).

    Cache ages are computed against time.time(). Older builds stored
    time.monotonic(), which is measured from boot and is typically a few
    million at most, so anything below this threshold cannot be a real date.
    """
    try:
        return float(value) >= _MIN_PLAUSIBLE_EPOCH
    except (TypeError, ValueError):
        return False


def _load_cache_from_db():
    """Load cached data from database on startup."""
    dropped = 0
    try:
        if not os.path.exists(_CACHE_DB_PATH):
            return

        conn = sqlite3.connect(_CACHE_DB_PATH)
        cursor = conn.cursor()

        # Load each cache type
        for cache_type, cache_dict in [
            ('rinex', _rinex_cache),
            ('tecsuite', _tecsuite_cache),
            ('abstec', _abstec_cache),
            ('parquet', _parquet_cache),
            ('parquet_sat', _parquet_sat_cache)
        ]:
            cursor.execute(
                'SELECT cache_key, data, timestamp FROM cache WHERE cache_type = ?',
                (cache_type,)
            )

            for row in cursor.fetchall():
                cache_key, data_json, timestamp = row
                if not _is_epoch_timestamp(timestamp):
                    # Written by an older build that stored time.monotonic(),
                    # which is measured from boot. Comparing it against
                    # time.time() would misreport the entry's age, so drop it
                    # and let the next request rescan.
                    dropped += 1
                    continue
                try:
                    data = json.loads(data_json)
                    cache_dict[cache_key] = (timestamp, data)
                except json.JSONDecodeError:
                    continue  # Skip corrupted entries

        conn.close()
        print(
            f"Loaded {sum(len(c) for c in [_rinex_cache, _tecsuite_cache, _abstec_cache, _parquet_cache, _parquet_sat_cache])} cache entries from database"
        )
        if dropped:
            print(f"Discarded {dropped} cache entries with pre-epoch (monotonic) timestamps")

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
_watcher_disabled: set[str] = set()
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
            logger.info(
                "[WATCHER] Change detected for %s via %s on %s",
                self.host_root,
                event.event_type,
                event.src_path,
            )


def _ensure_watcher(host_root: str, root: Path) -> None:
    """Start a watchdog observer for a root if watchdog is available."""
    if not WATCHDOG_AVAILABLE or not _WATCHERS_ENABLED:
        return

    with _watch_lock:
        if host_root in _watcher_disabled:
            return
        if host_root in _observers:
            return

        observer = Observer()
        observer.schedule(_RootChangeHandler(host_root), str(root), recursive=True)
        try:
            observer.start()
        except OSError as exc:
            # Common on Linux when the host hits fs.inotify.max_user_watches.
            # Watchers are an optimization only; the indexer must continue to serve requests.
            if getattr(exc, "errno", None) == errno.ENOSPC:
                logger.warning(
                    "[WATCHER] Failed to start observer for %s (inotify watch limit reached). "
                    "Continuing without filesystem watchers; indexing will rely on TTL/refresh.",
                    host_root,
                )
                _watcher_disabled.add(host_root)
                return
            logger.warning("[WATCHER] Failed to start observer for %s: %s", host_root, exc)
            _watcher_disabled.add(host_root)
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

    logger.info("[%s] Cache invalidated for %s - rescanning now", cache_type.upper(), host_root)
    return _scan_and_store(cache_type, host_root, root, scan_fn, cache_dict)


def _force_refresh_cache(
    cache_type: str,
    host_root: str,
    root: Path,
    scan_fn,
    cache_dict: dict,
):
    """Force a synchronous rescan and update the in-memory + persistent cache."""
    logger.info("[%s] Forced refresh for %s", cache_type.upper(), host_root)
    return _scan_and_store(cache_type, host_root, root, scan_fn, cache_dict)


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


_LOG_TAGS = {
    "rinex": "RINEX",
    "tecsuite": "TEC-SUITE",
    "abstec": "ABSTEC",
    "parquet": "PARQUET",
    "parquet_sat": "PARQUET-SAT",
}


def _cached_listing(
    cache_type: str,
    host_root: str,
    scan_fn,
    cache_dict: dict,
    *,
    refresh: bool = False,
    scan_subdir: str | None = None,
):
    """
    Listing of host_root served from cache_dict.

    Fresh entries are returned as-is; stale ones are returned while a
    background thread rescans; forced, invalidated (filesystem change) and
    missing entries are scanned now. scan_subdir, when it exists under the
    root, is scanned instead of the root itself; the cache key stays host_root.
    """
    tag = _LOG_TAGS[cache_type]
    if not host_root:
        return []
    root = Path(host_root)
    if not root.exists() or not root.is_dir():
        logger.warning("[%s] Root path does not exist or is not a directory: %s", tag, host_root)
        return []

    _ensure_watcher(host_root, root)
    scan_root = root / scan_subdir if scan_subdir and (root / scan_subdir).is_dir() else root
    if refresh:
        return _force_refresh_cache(cache_type, host_root, scan_root, scan_fn, cache_dict)
    invalidated_result = _refresh_invalidated_cache(cache_type, host_root, scan_root, scan_fn, cache_dict)
    if invalidated_result is not None:
        return invalidated_result

    cached_time, cached_result = cache_dict.get(host_root, (None, None))
    if cached_time is not None:
        cache_age = time.time() - cached_time
        if cache_age < _CACHE_TTL_SEC:
            logger.debug("[%s] Cache HIT (age: %.1fs, TTL: %ss)", tag, cache_age, _CACHE_TTL_SEC)
            return cached_result

        # Stale: answer with the old data and refresh without blocking the request.
        logger.info("[%s] Cache STALE (age: %.1fs) — serving old result, refreshing in background", tag, cache_age)
        _trigger_background_refresh(cache_type, host_root, scan_root, scan_fn, cache_dict)
        return cached_result

    logger.info("[%s] Cold start scan for %s", tag, host_root)
    return _scan_and_store(cache_type, host_root, scan_root, scan_fn, cache_dict)


def list_rinex_server_structure(host_root: str, refresh: bool = False) -> list[YearInfo]:
    return _cached_listing("rinex", host_root, _scan_rinex, _rinex_cache, refresh=refresh)


def _trigger_background_refresh(
    cache_type: str,
    host_root: str,
    root: Path,
    scan_fn,
    cache_dict: dict,
) -> None:
    """Kick off a background thread to refresh any cache without blocking the caller."""
    # Keyed by cache type as well: /parquet and /parquet-satellites share a root
    # but are separate scans.
    key = (cache_type, host_root)
    with _refresh_lock:
        if key in _refresh_in_progress:
            logger.debug(f"[{cache_type.upper()}] Refresh already in progress for {host_root}, skipping")
            return
        _refresh_in_progress.add(key)

    def _do_refresh():
        try:
            logger.info(f"[{cache_type.upper()}] Background refresh started for {host_root}")
            result = _scan_and_store(cache_type, host_root, root, scan_fn, cache_dict)
            logger.info(f"[{cache_type.upper()}] Background refresh complete — {len(result)} entries")
        except Exception as e:
            logger.error(f"[{cache_type.upper()}] Background refresh failed: {e}")
        finally:
            with _refresh_lock:
                _refresh_in_progress.discard(key)

    threading.Thread(target=_do_refresh, daemon=True).start()


def _scan_rinex(root: Path) -> list[YearInfo]:
    # Step 1: collect year directories
    with os.scandir(root) as it:
        year_entries = [e for e in it if e.is_dir() and YEAR_DIR_RE.fullmatch(e.name)]
    # Optional cap for very large datasets (keeps the newest years).
    if _MAX_YEARS > 0:
        year_entries.sort(key=lambda e: _year_sort_key(e.name), reverse=True)
        year_entries = year_entries[:_MAX_YEARS]

    # Step 2: each worker handles exactly ONE year_entry
    def scan_year(year_entry) -> YearInfo | None:
        days: list[DayInfo] = []

        with os.scandir(year_entry.path) as it:
            top_entries = sorted(
                [e for e in it if e.is_dir() and DAY_DIR_RE.fullmatch(e.name)],
                key=lambda e: e.name
            )

        for top_entry in top_entries:
            with os.scandir(top_entry.path) as it:
                entries = list(it)

            direct_zips = sum(
                1 for e in entries
                if e.is_file() and e.name.lower().endswith('.zip')
            )
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
                    stations = sum(1 for e in it2 if e.is_file() and e.name.lower().endswith('.zip'))
                days.append({"day": f"{top_entry.name}/{day_entry.name}", "stations": stations})

        if not days:
            return None

        days.sort(key=lambda item: _day_sort_key(item["day"]))
        return {"year": year_entry.name, "days": days}  # ← uses its OWN year_entry

    # Step 3: run all year scans in parallel
    with ThreadPoolExecutor(max_workers=_scan_workers()) as executor:
        results = list(executor.map(scan_year, year_entries))

    years = [r for r in results if r is not None]
    years.sort(key=lambda item: _year_sort_key(str(item["year"])), reverse=True)
    return years


def list_tecsuite_output_structure(host_root: str, refresh: bool = False) -> list[AbsTecYearInfo]:
    return _cached_listing("tecsuite", host_root, _scan_tecsuite_parallel, _tecsuite_cache, refresh=refresh, scan_subdir="in")


def list_abstec_output_structure(host_root: str, refresh: bool = False) -> list[AbsTecYearInfo]:
    """
    Return AbsTEC output structure under host_root.

    Expected layout:
      <root>/YYYY/DDD/SITE/...

    AbsTEC output does not necessarily contain `.dat` files, so we treat any
    non-empty SITE directory as indexed.
    """
    return _cached_listing("abstec", host_root, _scan_abstec_output_parallel, _abstec_cache, refresh=refresh)


def list_parquet_output_structure(host_root: str, refresh: bool = False) -> list[dict[str, object]]:
    return _cached_listing("parquet", host_root, _scan_parquet, _parquet_cache, refresh=refresh)


def list_parquet_satellite_structure(host_root: str, refresh: bool = False) -> list[dict[str, object]]:
    return _cached_listing("parquet_sat", host_root, _scan_parquet_satellites_parallel, _parquet_sat_cache, refresh=refresh)

def _scan_parquet(root: Path) -> list[dict[str, object]]:
    """Full filesystem scan for parquet output roots."""
    with os.scandir(root) as it:
        year_dirs = [Path(e.path) for e in it if e.is_dir() and ABSTEC_YEAR_DIR_RE.fullmatch(e.name)]
    if _MAX_YEARS > 0:
        year_dirs.sort(key=lambda p: int(p.name), reverse=True)
        year_dirs = year_dirs[:_MAX_YEARS]

    def scan_year(year_dir: Path) -> dict[str, object] | None:
        logger.debug(f"[PARQUET] Scanning year directory: {year_dir}")
        days: list[str] = []
        try:
            with os.scandir(year_dir) as it2:
                for entry in it2:
                    if entry.is_dir() and ABSTEC_DAY_DIR_RE.fullmatch(entry.name):
                        days.append(entry.name.zfill(3))
        except OSError:
            return None

        if not days:
            return None
        days.sort(key=lambda d: _abstec_day_sort_key(d))
        return {"year": year_dir.name, "days": days}

    with ThreadPoolExecutor(max_workers=_scan_workers()) as executor:
        results = list(executor.map(scan_year, year_dirs))

    years = [r for r in results if r is not None]
    years.sort(key=lambda item: int(str(item["year"])), reverse=True)
    return years


def _scan_abstec_output_parallel(scan_root: Path) -> list[AbsTecYearInfo]:
    """Parallel scan variant for AbsTEC output roots (YYYY/DDD/SITE/...)."""
    with os.scandir(scan_root) as it:
        year_dirs = [Path(e.path) for e in it if e.is_dir() and ABSTEC_YEAR_DIR_RE.fullmatch(e.name)]
    if _MAX_YEARS > 0:
        year_dirs.sort(key=lambda p: int(p.name), reverse=True)
        year_dirs = year_dirs[:_MAX_YEARS]

    def scan_year(year_dir: Path) -> AbsTecYearInfo | None:
        days: list[AbsTecDayInfo] = []
        try:
            with os.scandir(year_dir) as it2:
                day_dirs = [Path(e.path) for e in it2 if e.is_dir() and ABSTEC_DAY_DIR_RE.fullmatch(e.name)]
        except OSError:
            return None

        for day_dir in day_dirs:
            sites: list[str] = []
            try:
                with os.scandir(day_dir) as it3:
                    site_entries = [e for e in it3 if e.is_dir()]
            except OSError:
                continue

            for entry in site_entries:
                try:
                    has_any = any(True for _ in os.scandir(entry.path))
                except OSError:
                    has_any = False
                if has_any:
                    sites.append(entry.name)

            if sites:
                sites.sort()
                days.append({"day": day_dir.name.zfill(3), "sites": sites})

        if not days:
            return None

        days.sort(key=lambda item: _abstec_day_sort_key(item["day"]))
        return {"year": year_dir.name, "days": days}

    with ThreadPoolExecutor(max_workers=_scan_workers()) as executor:
        results = list(executor.map(scan_year, year_dirs))

    years = [r for r in results if r is not None]
    years.sort(key=lambda item: int(item["year"]), reverse=True)
    return years


def _scan_parquet_satellites(root: Path) -> list[dict[str, object]]:
    """Full filesystem scan for parquet roots with station/satellite extraction."""
    years: list[dict[str, object]] = []

    for year_dir in root.iterdir():
        if not year_dir.is_dir() or not ABSTEC_YEAR_DIR_RE.fullmatch(year_dir.name):
            continue

        logger.debug(f"[PARQUET-SAT] Scanning year directory: {year_dir}")
        days: list[dict[str, object]] = []
        for day_dir in year_dir.iterdir():
            if not day_dir.is_dir() or not ABSTEC_DAY_DIR_RE.fullmatch(day_dir.name):
                continue

            logger.debug(f"[PARQUET-SAT] Scanning day directory: {day_dir}")
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
                logger.debug(f"[PARQUET-SAT] Found {len(stations)} stations: {sorted(stations)[:5]}{'...' if len(stations) > 5 else ''}")
                # Sample the alphabetically first station dir for satellite IDs.
                # Satellite sets are uniform across stations on the same day.
                sample_dir = day_dir / min(stations)
                logger.debug(f"[PARQUET-SAT] Sampling satellites from: {sample_dir}")
                for pq_file in sample_dir.glob("*.parquet"):
                    stem = pq_file.stem.upper()
                    for match in SATELLITE_RE.findall(stem):
                        satellites.add(match)
            else:
                logger.debug(f"[PARQUET-SAT] Using flat layout with {len(flat_pq)} parquet files")
                # Flat layout: parquet files live directly under day_dir.
                for pq_file in flat_pq:
                    stem = pq_file.stem.upper()
                    for match in SATELLITE_RE.findall(stem):
                        satellites.add(match)

            if not stations and not satellites:
                continue

            logger.debug(f"[PARQUET-SAT] Day {day_dir.name} has {len(stations)} stations, {len(satellites)} satellites")
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


def _scan_parquet_satellites_parallel(root: Path) -> list[dict[str, object]]:
    """Parallel scan variant for parquet roots with station/satellite extraction."""
    with os.scandir(root) as it:
        year_dirs = [Path(e.path) for e in it if e.is_dir() and ABSTEC_YEAR_DIR_RE.fullmatch(e.name)]
    if _MAX_YEARS > 0:
        year_dirs.sort(key=lambda p: int(p.name), reverse=True)
        year_dirs = year_dirs[:_MAX_YEARS]

    def scan_year(year_dir: Path) -> dict[str, object] | None:
        days: list[dict[str, object]] = []
        try:
            with os.scandir(year_dir) as it2:
                day_dirs = [Path(e.path) for e in it2 if e.is_dir() and ABSTEC_DAY_DIR_RE.fullmatch(e.name)]
        except OSError:
            return None

        for day_dir in day_dirs:
            stations: set[str] = set()
            satellites: set[str] = set()
            flat_pq: list[Path] = []

            try:
                with os.scandir(day_dir) as it3:
                    for entry in it3:
                        if entry.is_dir():
                            stations.add(entry.name)
                        elif entry.is_file() and entry.name.lower().endswith(".parquet"):
                            flat_pq.append(Path(entry.path))
            except OSError:
                continue

            if stations:
                sample_dir = day_dir / min(stations)
                try:
                    with os.scandir(sample_dir) as it4:
                        for entry in it4:
                            if entry.is_file() and entry.name.lower().endswith(".parquet"):
                                stem = Path(entry.name).stem.upper()
                                for match in SATELLITE_RE.findall(stem):
                                    satellites.add(match)
                except OSError:
                    pass
            else:
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

        if not days:
            return None

        days.sort(key=lambda item: _abstec_day_sort_key(str(item["day"])))
        return {"year": year_dir.name, "days": days}

    with ThreadPoolExecutor(max_workers=_scan_workers()) as executor:
        results = list(executor.map(scan_year, year_dirs))

    years = [r for r in results if r is not None]
    years.sort(key=lambda item: int(str(item["year"])), reverse=True)
    return years


def _scan_tecsuite(scan_root: Path) -> list[AbsTecYearInfo]:
    """Full filesystem scan — called only when cache is cold or stale."""
    years: list[AbsTecYearInfo] = []

    for year_dir in scan_root.iterdir():
        if not year_dir.is_dir() or not ABSTEC_YEAR_DIR_RE.fullmatch(year_dir.name):
            continue

        logger.debug(f"[TEC-SUITE] Scanning year directory: {year_dir}")
        days: list[AbsTecDayInfo] = []
        for day_dir in year_dir.iterdir():
            if not day_dir.is_dir() or not ABSTEC_DAY_DIR_RE.fullmatch(day_dir.name):
                continue

            logger.debug(f"[TEC-SUITE] Scanning day directory: {day_dir}")
            sites: list[str] = []
            # Layout A: YYYY/DDD/SITE_DIR/*.dat  (site as subdirectory)
            for site_dir in day_dir.iterdir():
                if not site_dir.is_dir():
                    continue
                has_dat = any(
                    e.is_file() and e.name.lower().endswith('.dat')
                    for e in os.scandir(site_dir)
                )
                if has_dat:
                    logger.debug(f"[TEC-SUITE] Found site with .dat files: {site_dir.name}")
                    sites.append(site_dir.name)

            # Layout B: YYYY/DDD/SITE.dat  (flat – site name = file stem)
            if not sites:
                sites = [
                    entry.stem
                    for entry in day_dir.iterdir()
                    if entry.is_file() and entry.suffix.lower() == ".dat"
                ]
                if sites:
                    logger.debug(f"[TEC-SUITE] Found flat layout .dat files: {sites}")

            if sites:
                sites.sort()
                days.append({"day": day_dir.name.zfill(3), "sites": sites})

        days.sort(key=lambda item: _abstec_day_sort_key(item["day"]))
        if days:
            years.append({"year": year_dir.name, "days": days})

    years.sort(key=lambda item: int(item["year"]), reverse=True)
    return years


def _scan_tecsuite_parallel(scan_root: Path) -> list[AbsTecYearInfo]:
    """Parallel scan variant for TEC-suite DAT output roots."""
    with os.scandir(scan_root) as it:
        year_dirs = [Path(e.path) for e in it if e.is_dir() and ABSTEC_YEAR_DIR_RE.fullmatch(e.name)]
    if _MAX_YEARS > 0:
        year_dirs.sort(key=lambda p: int(p.name), reverse=True)
        year_dirs = year_dirs[:_MAX_YEARS]

    def scan_year(year_dir: Path) -> AbsTecYearInfo | None:
        days: list[AbsTecDayInfo] = []
        try:
            with os.scandir(year_dir) as it2:
                day_dirs = [Path(e.path) for e in it2 if e.is_dir() and ABSTEC_DAY_DIR_RE.fullmatch(e.name)]
        except OSError:
            return None

        for day_dir in day_dirs:
            sites: list[str] = []
            try:
                with os.scandir(day_dir) as it3:
                    day_entries = list(it3)
            except OSError:
                continue

            for entry in day_entries:
                if not entry.is_dir():
                    continue
                try:
                    has_dat = any(
                        e.is_file() and e.name.lower().endswith('.dat')
                        for e in os.scandir(entry.path)
                    )
                except OSError:
                    has_dat = False
                if has_dat:
                    sites.append(entry.name)

            if not sites:
                sites = [
                    Path(entry.name).stem
                    for entry in day_entries
                    if entry.is_file() and entry.name.lower().endswith(".dat")
                ]

            if sites:
                sites.sort()
                days.append({"day": day_dir.name.zfill(3), "sites": sites})

        if not days:
            return None

        days.sort(key=lambda item: _abstec_day_sort_key(item["day"]))
        return {"year": year_dir.name, "days": days}

    with ThreadPoolExecutor(max_workers=_scan_workers()) as executor:
        results = list(executor.map(scan_year, year_dirs))

    years = [r for r in results if r is not None]
    years.sort(key=lambda item: int(item["year"]), reverse=True)
    return years
    """Full filesystem scan — called only when cache is cold or stale."""
    years: list[AbsTecYearInfo] = []

    for year_dir in scan_root.iterdir():
        if not year_dir.is_dir() or not ABSTEC_YEAR_DIR_RE.fullmatch(year_dir.name):
            continue

        logger.debug(f"[TEC-SUITE] Scanning year directory: {year_dir}")
        days: list[AbsTecDayInfo] = []
        for day_dir in year_dir.iterdir():
            if not day_dir.is_dir() or not ABSTEC_DAY_DIR_RE.fullmatch(day_dir.name):
                continue

            logger.debug(f"[TEC-SUITE] Scanning day directory: {day_dir}")
            sites: list[str] = []
            # Layout A: YYYY/DDD/SITE_DIR/*.dat  (site as subdirectory)
            for site_dir in day_dir.iterdir():
                if not site_dir.is_dir():
                    continue
                has_dat = any(
                    e.is_file() and e.name.lower().endswith('.dat')
                    for e in os.scandir(site_dir)
                )
                if has_dat:
                    logger.debug(f"[TEC-SUITE] Found site with .dat files: {site_dir.name}")
                    sites.append(site_dir.name)

            # Layout B: YYYY/DDD/SITE.dat  (flat – site name = file stem)
            if not sites:
                sites = [
                    entry.stem
                    for entry in day_dir.iterdir()
                    if entry.is_file() and entry.suffix.lower() == ".dat"
                ]
                if sites:
                    logger.debug(f"[TEC-SUITE] Found flat layout .dat files: {sites}")

            if sites:
                sites.sort()
                days.append({"day": day_dir.name.zfill(3), "sites": sites})

        days.sort(key=lambda item: _abstec_day_sort_key(item["day"]))
        if days:
            years.append({"year": year_dir.name, "days": days})

    years.sort(key=lambda item: int(item["year"]), reverse=True)
    return years


def _scan_abstec_output(scan_root: Path) -> list[AbsTecYearInfo]:
    """Scan AbsTEC output roots (YYYY/DDD/SITE/...) without assuming `.dat` files."""
    years: list[AbsTecYearInfo] = []

    for year_dir in scan_root.iterdir():
        if not year_dir.is_dir() or not ABSTEC_YEAR_DIR_RE.fullmatch(year_dir.name):
            continue

        days: list[AbsTecDayInfo] = []
        for day_dir in year_dir.iterdir():
            if not day_dir.is_dir() or not ABSTEC_DAY_DIR_RE.fullmatch(day_dir.name):
                continue

            sites: list[str] = []
            for site_dir in day_dir.iterdir():
                if not site_dir.is_dir():
                    continue
                try:
                    has_any = any(True for _ in os.scandir(site_dir))
                except OSError:
                    has_any = False
                if has_any:
                    sites.append(site_dir.name)

            if sites:
                sites.sort()
                days.append({"day": day_dir.name.zfill(3), "sites": sites})

        days.sort(key=lambda item: _abstec_day_sort_key(item["day"]))
        if days:
            years.append({"year": year_dir.name, "days": days})

    years.sort(key=lambda item: int(item["year"]), reverse=True)
    return years
