"""
tec_map_lpi.py — Local polynomial interpolation (LPI) for the TEC map pipeline.

Moving weighted least squares with a degree-1 (local plane, default) or
degree-2 (local quadric) polynomial: for every target the surrounding IPP
samples are weighted with a Gaussian kernel over great-circle distance and
the polynomial is fitted; its value at the target is the field estimate.
Comparative ionosphere-mapping studies rank LPI and ordinary kriging as the
two most accurate local methods (Ogryzek et al., 2020).

The local basis is centred at each target (intercept = prediction) and scaled
by the kernel bandwidth, which keeps the normal equations well conditioned. A
scale-aware ridge on the non-intercept terms shrinks the fit towards a
weighted mean when the neighbourhood is degenerate (collinear stations), so
the estimator never extrapolates a wild gradient from a bad geometry. The
quadric needs 6 coefficients, so degree 2 applies only at targets with enough
effective neighbours; sparse targets silently drop to the degree-1 fit
(boundary behaviour then matches the default estimator).
"""

from __future__ import annotations

import logging

import numpy as np

from app.tec_map_kriging import EARTH_RADIUS_KM, _haversine_km

logger = logging.getLogger(__name__)

# Gaussian kernel sigma. Chosen between the mid-latitude TEC decorrelation
# length (80–130 km) and the coverage radius (300 km): wide enough that every
# in-coverage target keeps meaningful weight, narrow enough to preserve
# regional gradients.
DEFAULT_BANDWIDTH_KM = 200.0
# A plane needs 3 points; require one extra so the fit is a regression rather
# than an exact (noise-following) solve. Callers fall back to Delaunay-linear.
MIN_POINTS_FOR_LPI = 4
# Relative ridge on the slope terms of the weighted normal equations.
SLOPE_RIDGE_FRACTION = 1e-6
# Below this total kernel weight the target is effectively outside the data
# cloud; fall back to the nearest sample (the coverage mask hides it anyway).
MIN_TOTAL_WEIGHT = 1e-8
# Degree-2 fit (6 coefficients) is used only where the target has at least
# this many effective neighbours (kernel weight >= EFFECTIVE_WEIGHT, i.e.
# within ~2.1 sigma); everywhere else the degree-1 fit takes over.
MIN_POINTS_FOR_QUADRATIC = 7
EFFECTIVE_WEIGHT = 0.1


def _solve_local_fit(basis: np.ndarray, weights: np.ndarray, values: np.ndarray) -> np.ndarray:
    """Batched weighted least squares; returns the intercept (= prediction)."""
    k = basis.shape[-1]
    normal = np.einsum("mnp,mn,mnq->mpq", basis, weights, basis)   # (m, k, k)
    rhs = np.einsum("mnp,mn,n->mp", basis, weights, values)        # (m, k)

    ridge = SLOPE_RIDGE_FRACTION * (np.trace(normal, axis1=1, axis2=2) + 1e-12)
    for i in range(1, k):
        normal[:, i, i] += ridge
    normal += np.eye(k) * 1e-12  # keep zero-weight rows solvable; masked later

    try:
        beta = np.linalg.solve(normal, rhs)
    except np.linalg.LinAlgError:
        beta = np.einsum("mpq,mq->mp", np.linalg.pinv(normal), rhs)
    return beta[:, 0]


def lpi_interpolate(
    points_lon: np.ndarray,
    points_lat: np.ndarray,
    values: np.ndarray,
    grid_lon: np.ndarray,
    grid_lat: np.ndarray,
    bandwidth_km: float = DEFAULT_BANDWIDTH_KM,
    degree: int = 1,
) -> np.ndarray:
    """
    Local polynomial prediction of `values` at (grid_lon, grid_lat).

    degree=1 fits a local plane everywhere; degree=2 fits a local quadric at
    targets with enough effective neighbours and drops to the plane elsewhere.
    Targets may be a mesh or scattered points of any shape; one batched
    kxk solve per target, fully vectorized.
    """
    degree = 2 if int(degree) >= 2 else 1
    points_lon = np.asarray(points_lon, dtype=float)
    points_lat = np.asarray(points_lat, dtype=float)
    values = np.asarray(values, dtype=float)
    grid_shape = np.asarray(grid_lon).shape
    n = len(values)
    if n == 0:
        return np.full(grid_shape, np.nan)
    if n == 1:
        return np.full(grid_shape, float(values[0]))

    glon = np.deg2rad(np.asarray(grid_lon, dtype=float).ravel())
    glat = np.deg2rad(np.asarray(grid_lat, dtype=float).ravel())
    plon = np.deg2rad(points_lon)
    plat = np.deg2rad(points_lat)

    distances = _haversine_km(glon[:, None], glat[:, None], plon[None, :], plat[None, :])  # (m, n)
    sigma = max(float(bandwidth_km), 1e-6)
    weights = np.exp(-0.5 * (distances / sigma) ** 2)

    # Local east/north coordinates of the samples relative to each target,
    # scaled by the bandwidth so quadratic terms stay O(1) (conditioning).
    delta_lon = (plon[None, :] - glon[:, None] + np.pi) % (2.0 * np.pi) - np.pi
    u = EARTH_RADIUS_KM * delta_lon * np.cos(glat)[:, None] / sigma
    v = EARTH_RADIUS_KM * (plat[None, :] - glat[:, None]) / sigma

    ones = np.ones_like(u)
    plane_basis = np.stack([ones, u, v], axis=-1)                              # (m, n, 3)
    prediction = _solve_local_fit(plane_basis, weights, values)

    if degree == 2 and n >= MIN_POINTS_FOR_QUADRATIC:
        rich = (weights >= EFFECTIVE_WEIGHT).sum(axis=1) >= MIN_POINTS_FOR_QUADRATIC
        if rich.any():
            quad_basis = np.concatenate(
                [plane_basis[rich], np.stack([u[rich] ** 2, u[rich] * v[rich], v[rich] ** 2], axis=-1)],
                axis=-1,
            )                                                                  # (m_rich, n, 6)
            prediction[rich] = _solve_local_fit(quad_basis, weights[rich], values)

    total_weight = weights.sum(axis=1)
    far = total_weight < MIN_TOTAL_WEIGHT
    if far.any():
        prediction[far] = values[np.argmin(distances, axis=1)[far]]
    return prediction.reshape(grid_shape)
