"""Read named columns and missing-observation flags from satellite DAT files."""

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from .settings import TEC_MASKS


@dataclass(frozen=True)
class Track:
    """Valid time, elevation, azimuth, TEC and lock-loss samples from one file."""

    longitude: float
    latitude: float
    interval: float
    samples: NDArray[np.float64]
    rejected: int


def _number(value: str) -> float:
    parsed = float(value.replace("D", "E").replace("d", "e"))
    if not math.isfinite(parsed):
        raise ValueError("Non-finite observation")
    return parsed


def _indices(columns: list[str], parameter: str, extra_datetime_fields: int) -> tuple[list[int], bool]:
    time_name = next((key for key in ("hour", "ut", "tsn") if key in columns), "")
    required = [time_name, "el", "az", parameter, "validity"]
    if len(set(columns)) != len(columns) or not all(name and name in columns for name in required):
        raise ValueError(f"Columns must uniquely identify tsn or UT, el, az, {parameter} and validity")
    indices = [columns.index(name) for name in required]
    return [index + extra_datetime_fields * columns[:index].count("datetime") for index in indices], time_name == "tsn"


def read_track(path: Path, parameter: str) -> Track:
    """Read one DAT file using its own header and selected TEC combination."""
    missing_mask, slip_mask = TEC_MASKS[parameter]
    header: dict[str, str] = {}
    samples: list[tuple[float, float, float, float, bool]] = []
    positions: list[int] = []
    last_time = -1.0
    rejected = 0
    pending_slip = False
    interval = 30.0
    use_tsn = False
    line_number = 0
    try:
        with path.open(encoding="latin-1") as stream:
            for raw in stream:
                line_number += 1
                text = raw.strip().removeprefix("ï»¿")
                if not text:
                    continue
                if text.startswith("#"):
                    key, _, value = text[1:].partition(":")
                    header[key.strip().lower()] = value.strip()
                    continue
                if not positions:
                    columns_text = header.get("columns", "").lower()
                    columns = (
                        [name.strip() for name in columns_text.split(",")]
                        if "," in columns_text
                        else columns_text.split()
                    )
                    datetime_format = header.get("datetime format", "%Y-%m-%d %H:%M:%S")
                    positions, use_tsn = _indices(columns, parameter, max(0, len(datetime_format.split()) - 1))
                    interval = max(
                        _number(header.get("interval", "30").split()[0]),
                        _number(header.get("sampling interval", "0").split()[0]),
                    )
                    if interval <= 0:
                        raise ValueError("Observation interval must be positive")
                parts = text.split()
                if len(parts) <= max(positions):
                    raise ValueError("Observation lacks a required column")
                time, elevation, azimuth, tec, validity = (_number(parts[index]) for index in positions)
                if use_tsn:
                    if not time.is_integer():
                        raise ValueError("TSN must be an integer")
                    time *= interval
                else:
                    time *= 3600
                    nearest = round(time / interval) * interval
                    if abs(nearest - time) <= 0.019:
                        time = nearest
                if not 0 <= time <= 86400 or time <= last_time:
                    raise ValueError("Observation times must increase within the UTC day")
                if validity < 0 or not validity.is_integer():
                    raise ValueError("Validity must be a non-negative integer bitmask")
                if not -90 <= elevation <= 90 or not 0 <= azimuth <= 360:
                    raise ValueError("Elevation or azimuth is out of range")
                last_time = time
                flags = int(validity)
                pending_slip |= bool(flags & slip_mask)
                if flags & missing_mask or elevation < 0:
                    rejected += 1
                    continue
                samples.append((time, elevation, azimuth, tec, pending_slip))
                pending_slip = False
        if not positions:
            raise ValueError("No data records")
        longitude, latitude, _height = map(_number, header.get("position (l, b, h)", "").split(","))
        if not -180 <= longitude <= 360 or not -90 <= latitude <= 90:
            raise ValueError("Station longitude or latitude is out of range")
    except (IndexError, ValueError) as exc:
        raise ValueError(f"{path.name} line {line_number} — {exc}") from exc
    return Track(longitude, latitude, interval, np.asarray(samples, dtype=float).reshape(-1, 5), rejected)
