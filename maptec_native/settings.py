"""Shared CLI defaults and server-side validation for MapTEC."""

import calendar
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta

TEC_MASKS = {
    "tec.l": (24, 3),
    "tec.l1l2": (24, 3),
    "tec.l1l5": (8 | 16384, 1 | 65536),
    "tec.p": (96, 0),
    "tec.p1p2": (96, 0),
    "tec.p1p5": (32 | 8192, 0),
    "tec.c1p2": (68, 0),
    "tec.p2c1": (68, 0),
}


@dataclass(frozen=True)
class MapSettings:
    """Filtering, projection and rendering settings for a run."""

    max_absent_points: int = 2
    smoothing_minutes: float = 2.0
    detrend_minutes: float = 20.0
    skip_edge_samples: int = 5
    ut_start: float = 0.0
    ut_end: float = 24.0
    map_step_seconds: int = 30
    vertical: int = 1
    minimum_tec: float = 10.0
    tec_parameter: str = "tec.l1l2"
    shell_height_km: float = 300.0
    satellite: str = "0"
    min_lat: float = -90.0
    max_lat: float = 90.0
    min_lon: float = -180.0
    max_lon: float = 180.0
    color: int = 1
    point_size: float = 1.0
    color_min: float = -0.5
    color_max: float = 0.5
    di_threshold: float = 0.01


DEFAULTS = MapSettings()
LABELS = {
    "max_absent_points": "Maximum absent points",
    "smoothing_minutes": "Smoothing window (minutes)",
    "detrend_minutes": "Detrending window (minutes)",
    "skip_edge_samples": "Skipped samples at each arc edge",
    "ut_start": "UT start (hours)",
    "ut_end": "UT end (hours)",
    "map_step_seconds": "Map step (seconds)",
    "vertical": "Vertical TEC",
    "minimum_tec": "Minimum vertical TEC baseline (TECU)",
    "tec_parameter": "TEC column",
    "shell_height_km": "Shell height (km)",
    "satellite": "Satellite (0 for all, PRN or ID)",
    "min_lat": "Minimum latitude",
    "max_lat": "Maximum latitude",
    "min_lon": "Minimum longitude",
    "max_lon": "Maximum longitude",
    "color": "Color maps",
    "point_size": "Point size",
    "color_min": "Color minimum (TECU)",
    "color_max": "Color maximum (TECU)",
    "di_threshold": "Minimum absolute dI (TECU)",
}
OPTION_HELP = {
    "max_absent_points": "Break an arc when more consecutive samples are missing",
    "smoothing_minutes": "Use 0 to disable the short running average",
    "detrend_minutes": "Use a positive window at least as long as the smoothing window",
    "skip_edge_samples": "Discard this many observations from both ends before filtering",
    "ut_start": "Use decimal hours from 0 to 24, e.g. 1.5 for 01:30",
    "ut_end": "Include the final epoch when it falls on the map grid",
    "map_step_seconds": "Use a whole number of seconds on a grid starting at midnight",
    "vertical": "Use 1 to project slant TEC vertically or 0 to retain slant TEC",
    "minimum_tec": "Sets the vertical baseline at the minimum slant-TEC observation",
    "tec_parameter": "Use a column present in the DAT header, e.g. tec.l1l2 or tec.l",
    "shell_height_km": "Used for both pierce points and the optional solar terminator",
    "satellite": "Use G01 for GPS 01 or 1 to include PRN 01 across constellations",
    "min_lat": "Southern map boundary in degrees",
    "max_lat": "Northern map boundary in degrees",
    "min_lon": "Western map boundary in degrees",
    "max_lon": "Eastern map boundary in degrees",
    "color": "Use 1 for the color palette or 0 for grayscale",
    "point_size": "Scale the plotted point area relative to the default",
    "color_min": "Values below this dI use the lowest palette color",
    "color_max": "Values above this dI use the highest palette color",
    "di_threshold": "Omit points whose absolute dI is smaller than this value",
}


def parse_settings(values: Mapping[str, object]) -> MapSettings:
    """Validate text from either the command line or a form submission."""
    parsed = {}
    for key, label in LABELS.items():
        default = getattr(DEFAULTS, key)
        value = str(values.get(key, default)).strip() or str(default)
        try:
            parsed[key] = type(default)(value)
        except ValueError as exc:
            raise ValueError(
                f"{label} must be {'an integer' if isinstance(default, int) else 'a finite number'}"
            ) from exc
        if isinstance(default, float) and not math.isfinite(parsed[key]):
            raise ValueError(f"{label} must be a finite number")
    parsed["tec_parameter"] = parsed["tec_parameter"].lower()
    parsed["satellite"] = parsed["satellite"].upper()
    settings = MapSettings(**parsed)
    if settings.tec_parameter not in TEC_MASKS:
        raise ValueError("TEC column must be a supported tec-suite parameter")
    if not re.fullmatch(r"(?:ALL|[A-Z]?[0-9]{1,3})", settings.satellite):
        raise ValueError("Satellite must be 0, all, a numeric PRN, or a satellite ID such as G01")
    if settings.max_absent_points < 0 or settings.skip_edge_samples < 0:
        raise ValueError("Maximum absent points and skipped samples must be non-negative")
    if not 0 <= settings.smoothing_minutes <= settings.detrend_minutes <= 1440 or settings.detrend_minutes == 0:
        raise ValueError(
            "Filtering windows must satisfy 0 ≤ smoothing ≤ detrending ≤ 1440 minutes with positive detrending"
        )
    if not 0 <= settings.ut_start <= settings.ut_end <= 24:
        raise ValueError("UT bounds must be ordered within 0 to 24 hours")
    if not 1 <= settings.map_step_seconds <= 86400:
        raise ValueError("Map step must be from 1 to 86400 seconds")
    if settings.vertical not in (0, 1) or settings.color not in (0, 1):
        raise ValueError("Vertical TEC and color must be 0 or 1")
    if not 0 < settings.shell_height_km <= 2000:
        raise ValueError("Shell height must be positive and at most 2000 km")
    if not -90 <= settings.min_lat < settings.max_lat <= 90:
        raise ValueError("Latitude bounds must be increasing within -90 to 90 degrees")
    if not -180 <= settings.min_lon < settings.max_lon <= 180:
        raise ValueError("Longitude bounds must be increasing within -180 to 180 degrees")
    if settings.color_min >= settings.color_max:
        raise ValueError("Color minimum must be less than color maximum")
    if settings.point_size <= 0 or settings.di_threshold < 0:
        raise ValueError("Point size must be positive and the dI threshold must be non-negative")
    return settings


def parse_selection(year: str, day: str, days: str, site: str) -> tuple[list[date], list[str]]:
    """Resolve a year/day selection and optional comma-separated station names."""
    if not re.fullmatch(r"[0-9]{4}", year) or not 2 <= int(year) <= 9998:
        raise ValueError("Year must be a four-digit calendar year between 0002 and 9998")
    if bool(day) == bool(days):
        raise ValueError("Select either a single day or a batch of days")
    if day and not re.fullmatch(r"[0-9]{1,3}", day):
        raise ValueError("Day of year must be an integer")
    if days and not re.fullmatch(r"[0-9]{1,3}(?:-[0-9]{1,3})?(?:,[0-9]{1,3}(?:-[0-9]{1,3})?)*", days):
        raise ValueError("Batch days must be comma-separated days or inclusive ranges")
    first = date(int(year), 1, 1)
    limit = 365 + calendar.isleap(first.year)
    selected: set[int] = set()
    for part in (day or days).split(","):
        ends = list(map(int, part.split("-")))
        if not 1 <= ends[0] <= ends[-1] <= limit:
            raise ValueError(f"Select increasing day ranges within 1 to {limit}")
        selected.update(range(ends[0], ends[-1] + 1))
    sites = list(dict.fromkeys(piece.strip() for piece in site.split(","))) if site else []
    if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}", name) for name in sites):
        raise ValueError("Site selection must contain comma-separated station directory names")
    return [first + timedelta(days=value - 1) for value in sorted(selected)], sites
