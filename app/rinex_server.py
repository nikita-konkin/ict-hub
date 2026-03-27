"""
rinex_server.py — helpers for browsing TEC-suite RINEX server folders.

Expected layout:
    <host_root>/YYYY_original/DD|DDD/<station>.zip

Only folders matching YYYY_original are considered years, and only 2-digit or
3-digit day folders are considered valid days.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import TypedDict

YEAR_DIR_RE = re.compile(r"^\d{4}_original$")
DAY_DIR_RE = re.compile(r"^\d{3}$")
DAY_DIR_RE = re.compile(r"^\d{2,3}$")


class DayInfo(TypedDict):
    day: str
    stations: int


class YearInfo(TypedDict):
    year: str
    days: list[DayInfo]


def _day_sort_key(name: str) -> tuple[int, int, str]:
    """Sort days numerically while keeping deterministic order for equal values."""
    return (int(name), len(name), name)


def _year_sort_key(name: str) -> int:
    """Sort years numerically by their 4-digit prefix."""
    return int(name[:4])


def list_rinex_server_structure(host_root: str) -> list[YearInfo]:
    """
    Return discovered structure under host_root.

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
