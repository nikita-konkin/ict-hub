"""
data_browser.py — helpers for browsing RINEX server and TEC-suite DAT output folders.

Provides two filesystem scanners used by the run-page dropdowns:

  list_rinex_server_structure()
      Scans a RINEX server root for year/day/zip archives.
      Expected layout: <root>/YYYY_original/DD|DDD/<station>.zip

  list_tecsuite_output_structure()
      Scans a TEC-suite output root for year/day/site DAT files.
      Expected layouts:
        <root>/YYYY/DDD/SITE/*.dat
        <root>/in/YYYY/DDD/SITE/*.dat

Caching:
  Both functions cache their last result keyed by the root path.  The cache
  entry is invalidated when the scanned directory's mtime changes (i.e. a
  folder is added or removed directly under that directory).  Switching
  between converters without any filesystem changes is therefore instant.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import TypedDict

# (path → (mtime, result)) — module-level, lives for the process lifetime
_rinex_cache: dict[str, tuple[float, list]] = {}
_tecsuite_cache: dict[str, tuple[float, list]] = {}

YEAR_DIR_RE = re.compile(r"^\d{4}_original$")
DAY_DIR_RE = re.compile(r"^\d{2,3}$")
ABSTEC_YEAR_DIR_RE = re.compile(r"^\d{4}$")
ABSTEC_DAY_DIR_RE = re.compile(r"^\d{1,3}$")


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
    """Sort days numerically while keeping deterministic order for equal values."""
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

    Results are cached and reused as long as the root directory mtime is
    unchanged (i.e. no year folders have been added or removed).

    Output shape:
      [
        {
          "year": "2026_original",
          "days": [
            {"day": "001", "stations": 15},
            ...
          ],
        },
        ...
      ]
    """
    if not host_root:
        return []
    root = Path(host_root)
    if not root.exists() or not root.is_dir():
        return []

    try:
        mtime = root.stat().st_mtime
    except OSError:
        return []

    cached_mtime, cached_result = _rinex_cache.get(host_root, (None, None))
    if mtime == cached_mtime:
        return cached_result  # type: ignore[return-value]

    result = _scan_rinex(root)
    _rinex_cache[host_root] = (mtime, result)
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
        for day_dir in year_dir.iterdir():
            if not day_dir.is_dir():
                continue
            if not DAY_DIR_RE.fullmatch(day_dir.name):
                continue

            stations = sum(
                1
                for entry in day_dir.iterdir()
                if entry.is_file() and entry.suffix.lower() == ".zip"
            )
            days.append({"day": day_dir.name, "stations": stations})

        days.sort(key=lambda item: _day_sort_key(item["day"]))
        years.append({"year": year_dir.name, "days": days})

    years.sort(key=lambda item: _year_sort_key(str(item["year"])), reverse=True)
    return years


def list_tecsuite_output_structure(host_root: str) -> list[AbsTecYearInfo]:
    """
    Return TEC-suite DAT output structure for AbsTEC selection UI.

    Results are cached and reused as long as the scanned directory mtime is
    unchanged (i.e. no year folders have been added or removed).

    Expected layouts:
      <root>/YYYY/DDD/SITE/*.dat
      <root>/in/YYYY/DDD/SITE/*.dat
    """
    if not host_root:
        return []
    root = Path(host_root)
    if not root.exists() or not root.is_dir():
        return []

    scan_root = root / "in" if (root / "in").is_dir() else root
    try:
        mtime = scan_root.stat().st_mtime
    except OSError:
        return []

    cached_mtime, cached_result = _tecsuite_cache.get(host_root, (None, None))
    if mtime == cached_mtime:
        return cached_result  # type: ignore[return-value]

    result = _scan_tecsuite(scan_root)
    _tecsuite_cache[host_root] = (mtime, result)
    return result


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
            for site_dir in day_dir.iterdir():
                if not site_dir.is_dir():
                    continue
                has_dat = any(
                    entry.is_file() and entry.suffix.lower() == ".dat"
                    for entry in site_dir.rglob("*")
                )
                if has_dat:
                    sites.append(site_dir.name)

            if sites:
                sites.sort()
                days.append({"day": day_dir.name.zfill(3), "sites": sites})

        days.sort(key=lambda item: _abstec_day_sort_key(item["day"]))
        if days:
            years.append({"year": year_dir.name, "days": days})

    years.sort(key=lambda item: int(item["year"]), reverse=True)
    return years
