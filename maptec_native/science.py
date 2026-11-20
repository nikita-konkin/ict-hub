"""Thin-shell projection, centered TEC filtering and solar shadow geometry."""

import calendar
from datetime import date

import numpy as np
from numpy.typing import NDArray

EARTH_RADIUS_KM = 6371.0


def shell_coordinates(
    elevation: NDArray[np.float64],
    azimuth: NDArray[np.float64],
    latitude: float,
    longitude: float,
    height: float,
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    """Return pierce-point latitude, longitude and the slant-to-vertical factor."""
    el, az = np.deg2rad(elevation), np.deg2rad(azimuth)
    lat = np.deg2rad(latitude)
    sin_zenith = EARTH_RADIUS_KM / (EARTH_RADIUS_KM + height) * np.cos(el)
    ipp_radius = 6372.0
    angle = np.pi / 2 - el - np.arcsin(ipp_radius / (ipp_radius + height) * np.cos(el))
    ipp_lat = np.arcsin(np.clip(np.sin(lat) * np.cos(angle) + np.cos(lat) * np.sin(angle) * np.cos(az), -1, 1))
    delta_lon = np.arctan2(
        np.sin(angle) * np.sin(az),
        np.cos(lat) * np.cos(angle) - np.sin(lat) * np.sin(angle) * np.cos(az),
    )
    return np.rad2deg(ipp_lat), (longitude + np.rad2deg(delta_lon) + 180) % 360 - 180, np.sqrt(1 - sin_zenith**2)


def _running_mean(values: NDArray[np.float64], half_window: int) -> NDArray[np.float64]:
    index = np.arange(len(values))
    radius = np.minimum(np.minimum(index, len(values) - 1 - index), half_window)
    sums = np.r_[0.0, np.cumsum(values - values[0])]
    return values[0] + (sums[index + radius + 1] - sums[index - radius]) / (2 * radius + 1)


def level_arc(
    values: NDArray[np.float64], factor: NDArray[np.float64], minimum: float, vertical: bool
) -> NDArray[np.float64]:
    """Use the angle at minimum slant TEC to set the vertical baseline."""
    lowest = int(np.argmin(values))
    leveled = values - values[lowest] + minimum / factor[lowest]
    return leveled * factor if vertical else leveled


def filter_arc(
    values: NDArray[np.float64], cadence: float, smoothing: float, detrending: float
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Smooth an evenly spaced arc and subtract its longer centered mean."""
    short = max(0, (int(smoothing * 60 / cadence) - 1) // 2)
    long = max(0, (int(detrending * 60 / cadence) - 1) // 2)
    smooth = _running_mean(values, short)
    return smooth, smooth - _running_mean(smooth, long)


def terminator(day: date, seconds: float, height: float) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Compute the small circle where sunlight grazes Earth at the given shell."""
    hours = seconds / 3600
    gamma = 2 * np.pi / (365 + calendar.isleap(day.year)) * (day.timetuple().tm_yday - 1 + (hours - 12) / 24)
    equation_minutes = 229.18 * (
        0.000075
        + 0.001868 * np.cos(gamma)
        - 0.032077 * np.sin(gamma)
        - 0.014615 * np.cos(2 * gamma)
        - 0.040849 * np.sin(2 * gamma)
    )
    declination = (
        0.006918
        - 0.399912 * np.cos(gamma)
        + 0.070257 * np.sin(gamma)
        - 0.006758 * np.cos(2 * gamma)
        + 0.000907 * np.sin(2 * gamma)
        - 0.002697 * np.cos(3 * gamma)
        + 0.00148 * np.sin(3 * gamma)
    )
    subsolar_lon = np.deg2rad(180 - 15 * hours - equation_minutes / 4)
    cos_lon, sin_lon = np.cos(subsolar_lon), np.sin(subsolar_lon)
    cos_dec, sin_dec = np.cos(declination), np.sin(declination)
    sun = np.array([cos_dec * cos_lon, cos_dec * sin_lon, sin_dec])
    east = np.array([-sin_lon, cos_lon, 0])
    north = np.cross(sun, east)
    phi = np.linspace(0, 2 * np.pi, 721)
    ratio = EARTH_RADIUS_KM / (EARTH_RADIUS_KM + height)
    xyz = -np.sqrt(1 - ratio**2) * sun[:, None] + ratio * (east[:, None] * np.cos(phi) + north[:, None] * np.sin(phi))
    x = np.rad2deg(np.arctan2(xyz[1], xyz[0]))
    y = np.rad2deg(np.arcsin(np.clip(xyz[2], -1, 1)))
    breaks = np.flatnonzero(np.abs(np.diff(x)) > 180) + 1
    return np.insert(x, breaks, np.nan), np.insert(y, breaks, np.nan)
