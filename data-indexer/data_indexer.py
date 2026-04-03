"""
data_indexer.py — data indexing service for RINEX, TEC-suite, and AbsTEC data.

Provides REST endpoints that return XML structures for:
- RINEX server structure
- TEC-suite DAT output structure (in/out)
- AbsTEC output structure (in/out)
- Parquet output structure

All endpoints return XML responses for consumption by other services.
"""

import re
from pathlib import Path
from typing import TypedDict
from flask import Flask, request, Response
import dicttoxml

# (path → (mtime, result)) — module-level, lives for the process lifetime
_rinex_cache: dict[str, tuple[float, list]] = {}
_tecsuite_cache: dict[str, tuple[float, list]] = {}
_parquet_cache: dict[str, tuple[float, list]] = {}

YEAR_DIR_RE = re.compile(r"^\d{4}_original$")
DAY_DIR_RE = re.compile(r"^\d{2,3}$")
MONTH_DIR_RE = re.compile(r"^\d{2}$")
DAY_IN_MONTH_RE = re.compile(r"^\d{2}$")
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

    Results are cached and reused as long as the root directory mtime is
    unchanged (i.e. no year folders have been added or removed).

    Layout before 2019: <root>/YYYY_original/DOY/<station>.zip
    Layout from 2019: <root>/YYYY_original/MM/DD/<station>.zip
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

        year_num = int(year_dir.name[:4])
        days: list[DayInfo] = []

        if year_num >= 2019:
            # New structure: YYYY_original/MM/DD/<station>.zip
            for month_dir in sorted(year_dir.iterdir(), key=lambda d: d.name):
                if not month_dir.is_dir():
                    continue
                if not MONTH_DIR_RE.fullmatch(month_dir.name):
                    continue
                month_num = int(month_dir.name)
                if not 1 <= month_num <= 12:
                    continue

                for day_dir in sorted(month_dir.iterdir(), key=lambda d: d.name):
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
                    days.append({"day": f"{month_dir.name}/{day_dir.name}", "stations": stations})
        else:
            # Old structure: YYYY_original/DOY/<station>.zip
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


def list_parquet_output_structure(host_root: str) -> list[dict[str, object]]:
    """
    Return parquet output structure under host_root for the year/day UI.

    Results are cached and reused as long as the scanned directory mtime is
    unchanged.

    Expected layout (mirrors the DAT source root):
      <root>/YYYY/DDD/…   (any files/subdirs below DDD are ignored)
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

    cached_mtime, cached_result = _parquet_cache.get(host_root, (None, None))
    if mtime == cached_mtime:
        return cached_result  # type: ignore[return-value]

    result = _scan_parquet(root)
    _parquet_cache[host_root] = (mtime, result)
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