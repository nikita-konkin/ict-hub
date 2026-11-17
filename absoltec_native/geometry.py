"""Thin-shell geometry used by the published TayAbsTEC model (angles in degrees)."""

from __future__ import annotations

import numpy as np
from numpy.typing import ArrayLike, NDArray

EARTH_RADIUS_KM = 6371.0
SHELL_HEIGHT_KM = 450.0


def _elevations(value: ArrayLike) -> NDArray[np.float64]:
    elevation = np.asarray(value, dtype=float)
    if not np.all(np.isfinite(elevation)) or np.any((elevation < 0) | (elevation > 90)):
        raise ValueError("Elevation must be finite and between 0 and 90 degrees")
    return elevation


def mapping_factor(
    elevation_deg: ArrayLike,
    *,
    correction: float = 0.97,
    shell_height_km: float = SHELL_HEIGHT_KM,
    earth_radius: float = EARTH_RADIUS_KM,
) -> NDArray[np.float64]:
    """Slant/vertical factor, with correction inside the zenith-angle sine.

    Source: Yasyukevich et al. (2015), DOI 10.1134/S001679321506016X.
    The 2017 executable also contains the 6371/450-km shell geometry.
    """
    elevation = _elevations(elevation_deg)
    if not np.isfinite(correction) or not 0 < correction <= 1:
        raise ValueError("Correction coefficient must be finite and in (0, 1]")
    if not np.isfinite(shell_height_km) or shell_height_km <= 0:
        raise ValueError("Shell height must be finite and positive")
    if not np.isfinite(earth_radius) or earth_radius <= 0:
        raise ValueError("Earth radius must be finite and positive")
    sine = earth_radius / (earth_radius + shell_height_km) * np.sin(np.deg2rad(correction * (90.0 - elevation)))
    return np.asarray(1.0 / np.sqrt(1.0 - sine**2))


def pierce_point(
    elevation_deg: ArrayLike,
    azimuth_deg: ArrayLike,
    latitude_deg: float,
    longitude_deg: float,
    *,
    shell_height_km: float = SHELL_HEIGHT_KM,
    legacy_longitude: bool = False,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Return IPP latitude and longitude on a spherical ionospheric shell.

    Azimuth is clockwise from north. atan2 retains the longitude quadrant,
    including polar and dateline crossings. Station height is not used by
    the published spherical-Earth approximation.
    """
    elevation = _elevations(elevation_deg)
    azimuth = np.asarray(azimuth_deg, dtype=float)
    if not np.all(np.isfinite(azimuth)):
        raise ValueError("Azimuth must be finite")
    if not np.isfinite(latitude_deg) or not -90 <= latitude_deg <= 90:
        raise ValueError("Station latitude must be between -90 and 90 degrees")
    if not np.isfinite(longitude_deg):
        raise ValueError("Station longitude must be finite")
    if not np.isfinite(shell_height_km) or shell_height_km <= 0:
        raise ValueError("Shell height must be finite and positive")
    el, az = np.deg2rad(elevation), np.deg2rad(azimuth)
    lat = np.deg2rad(latitude_deg)
    lon = np.deg2rad(longitude_deg)
    central_angle = np.pi / 2 - el - np.arcsin(EARTH_RADIUS_KM / (EARTH_RADIUS_KM + shell_height_km) * np.cos(el))
    ipp_lat = np.arcsin(
        np.clip(np.sin(lat) * np.cos(central_angle) + np.cos(lat) * np.sin(central_angle) * np.cos(az), -1, 1)
    )
    if legacy_longitude:
        # The recovered 2017 helper at 0x401bf0 uses asin, losing the quadrant
        # when a pierce point crosses a pole. Retained only for comparison.
        ipp_lon = lon + np.arcsin(np.clip(np.sin(central_angle) * np.sin(az) / np.cos(ipp_lat), -1, 1))
    else:
        ipp_lon = lon + np.arctan2(
            np.sin(central_angle) * np.sin(az),
            np.cos(lat) * np.cos(central_angle) - np.sin(lat) * np.sin(central_angle) * np.cos(az),
        )
    return np.rad2deg(ipp_lat), (np.rad2deg(ipp_lon) + 180) % 360 - 180


def longitude_offset(longitude_deg: ArrayLike, origin_deg: float) -> NDArray[np.float64]:
    """Shortest signed longitude offset; prevents a 360-degree jump at the dateline."""
    return np.asarray((np.asarray(longitude_deg, dtype=float) - origin_deg + 180) % 360 - 180)


def turbotec_alpha(latitude_deg: float) -> float:
    """Latitude-dependent MSLM correcting coefficient, TuRBOTEC Table 1.

    Yasyukevich, Mylnikova and Vesnin (Sensors 20(19):5702, 2020) tabulate
    alpha = 0.87 for an equatorial station (1.3 deg), 0.97 for a mid-latitude
    station (52.2 deg) and 0.94 for a high-latitude station (76.5 deg). The
    paper does not publish band boundaries; these thresholds place the three
    tabulated stations inside their bands.
    """
    if not np.isfinite(latitude_deg) or not -90 <= latitude_deg <= 90:
        raise ValueError("Latitude must be finite and between -90 and 90 degrees")
    absolute = abs(float(latitude_deg))
    if absolute < 25.0:
        return 0.87
    if absolute > 65.0:
        return 0.94
    return 0.97
