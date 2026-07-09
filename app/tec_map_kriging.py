"""
tec_map_kriging.py — Ordinary kriging interpolation for the TEC map pipeline.

Per-frame ordinary kriging on the sphere with an exponential variogram
gamma(h) = nugget + sill * (1 - exp(-h / range_km)) fitted to the frame's own
samples. When the fit is not possible (few points, degenerate geometry) the
parameters fall back to literature-based defaults: range 300 km (mid-latitude
TEC decorrelation scale is 80–130 km for small-scale variability, with useful
correlation extending to hundreds of km) and nugget = 5% of sample variance.

Compared with the default Delaunay-linear interpolation, kriging weights noisy
samples through the nugget effect and extrapolates towards the field mean
outside the sample cloud instead of producing nearest-neighbour plateaus.
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)

EARTH_RADIUS_KM = 6371.0
DEFAULT_RANGE_KM = 300.0
MIN_RANGE_KM = 50.0
MAX_RANGE_KM = 2000.0
DEFAULT_NUGGET_FRACTION = 0.05
# Below this many samples the kriging system is too poorly constrained;
# callers should fall back to Delaunay-linear interpolation.
MIN_POINTS_FOR_KRIGING = 8
MIN_POINTS_FOR_FIT = 12
VARIOGRAM_BINS = 12


def _haversine_km(lon1_rad, lat1_rad, lon2_rad, lat2_rad):
    dlat = lat1_rad - lat2_rad
    dlon = lon1_rad - lon2_rad
    h = np.sin(dlat / 2.0) ** 2 + np.cos(lat1_rad) * np.cos(lat2_rad) * np.sin(dlon / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(h, 0.0, 1.0)))


def pairwise_distances_km(lon_deg: np.ndarray, lat_deg: np.ndarray) -> np.ndarray:
    lon = np.deg2rad(np.asarray(lon_deg, dtype=float))
    lat = np.deg2rad(np.asarray(lat_deg, dtype=float))
    return _haversine_km(lon[:, None], lat[:, None], lon[None, :], lat[None, :])


def exponential_variogram(h_km, nugget: float, sill: float, range_km: float):
    h = np.asarray(h_km, dtype=float)
    return nugget + sill * (1.0 - np.exp(-h / max(float(range_km), 1e-6)))


def fit_exponential_variogram(distances_km: np.ndarray, values: np.ndarray) -> tuple[float, float, float]:
    """
    Fit (nugget, partial sill, range_km) to the empirical semivariogram.
    Returns literature-based fallback parameters when a stable fit is not
    possible; never raises.
    """
    values = np.asarray(values, dtype=float)
    variance = float(np.var(values))
    if not np.isfinite(variance) or variance <= 0.0:
        variance = 1e-6
    fallback = (DEFAULT_NUGGET_FRACTION * variance, variance, DEFAULT_RANGE_KM)

    n = len(values)
    if n < MIN_POINTS_FOR_FIT:
        return fallback

    upper = np.triu_indices(n, k=1)
    h = distances_km[upper]
    gamma = 0.5 * (values[:, None] - values[None, :])[upper] ** 2

    max_h = float(np.quantile(h, 0.7)) if h.size else 0.0
    if max_h <= 0.0:
        return fallback

    bin_edges = np.linspace(0.0, max_h, VARIOGRAM_BINS + 1)
    bin_index = np.digitize(h, bin_edges) - 1
    h_binned: list[float] = []
    gamma_binned: list[float] = []
    for b in range(VARIOGRAM_BINS):
        selection = bin_index == b
        if int(selection.sum()) >= 3:
            h_binned.append(float(np.mean(h[selection])))
            gamma_binned.append(float(np.mean(gamma[selection])))
    if len(h_binned) < 4:
        return fallback

    try:
        from scipy.optimize import curve_fit

        lower = np.array([0.0, 1e-9, MIN_RANGE_KM])
        upper_b = np.array([2.0 * variance + 1e-6, 10.0 * variance + 1e-6, MAX_RANGE_KM])
        p0 = np.clip(np.array(fallback), lower, upper_b)
        popt, _ = curve_fit(
            exponential_variogram,
            np.asarray(h_binned),
            np.asarray(gamma_binned),
            p0=p0,
            bounds=(lower, upper_b),
            maxfev=2000,
        )
        nugget, sill, range_km = (float(v) for v in popt)
        if not all(np.isfinite([nugget, sill, range_km])) or sill <= 0.0:
            return fallback
        return nugget, sill, range_km
    except Exception:
        return fallback


def kriging_interpolate(
    points_lon: np.ndarray,
    points_lat: np.ndarray,
    values: np.ndarray,
    grid_lon: np.ndarray,
    grid_lat: np.ndarray,
    variogram: tuple[float, float, float] | None = None,
) -> np.ndarray:
    """
    Ordinary kriging prediction of `values` on the (grid_lon, grid_lat) mesh.

    Solves the standard OK system [[Gamma, 1], [1^T, 0]] w = [gamma*, 1] with
    the fitted exponential variogram; one factorization serves all grid cells.
    `variogram` supplies pre-fitted (nugget, sill, range_km) — leave-one-out
    validation fits once per frame and reuses the parameters for every subset.
    """
    points_lon = np.asarray(points_lon, dtype=float)
    points_lat = np.asarray(points_lat, dtype=float)
    values = np.asarray(values, dtype=float)
    n = len(values)
    if n < 2:
        return np.full(grid_lon.shape, float(values[0]) if n else np.nan)

    d_pp = pairwise_distances_km(points_lon, points_lat)
    if variogram is not None:
        nugget, sill, range_km = (float(v) for v in variogram)
    else:
        nugget, sill, range_km = fit_exponential_variogram(d_pp, values)
    logger.debug("kriging variogram: nugget=%.3g sill=%.3g range=%.0f km (n=%d)", nugget, sill, range_km, n)

    gamma_pp = exponential_variogram(d_pp, nugget, sill, range_km)
    np.fill_diagonal(gamma_pp, 0.0)

    system = np.zeros((n + 1, n + 1))
    system[:n, :n] = gamma_pp
    # Tiny diagonal jitter keeps the system invertible with duplicate IPPs.
    system[:n, :n] += np.eye(n) * (1e-9 * max(sill, 1.0))
    system[:n, n] = 1.0
    system[n, :n] = 1.0

    grid_shape = grid_lon.shape
    glon = np.deg2rad(np.asarray(grid_lon, dtype=float).ravel())
    glat = np.deg2rad(np.asarray(grid_lat, dtype=float).ravel())
    plon = np.deg2rad(points_lon)
    plat = np.deg2rad(points_lat)
    d_gp = _haversine_km(glon[:, None], glat[:, None], plon[None, :], plat[None, :])  # (m, n)

    rhs = np.empty((n + 1, d_gp.shape[0]))
    rhs[:n, :] = exponential_variogram(d_gp, nugget, sill, range_km).T
    rhs[n, :] = 1.0

    try:
        weights = np.linalg.solve(system, rhs)
    except np.linalg.LinAlgError:
        weights, *_ = np.linalg.lstsq(system, rhs, rcond=None)

    prediction = weights[:n, :].T @ values
    return prediction.reshape(grid_shape)
