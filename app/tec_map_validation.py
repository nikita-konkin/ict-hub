"""
tec_map_validation.py — Leave-one-station-out (LOSO) accuracy validation
for the TEC map pipeline.

Implements map-quality criterion #1 ("accuracy at reference points"): each
station's frame sample is excluded in turn, the field is predicted at its IPP
from the remaining stations, and the prediction error quantifies how well the
map reconstructs values the interpolation has not seen. Errors are reported in
TECU on the VTEC field regardless of the rendered field — derived fields
(gdd, b_k) are deterministic transforms of VTEC, so VTEC accuracy is the
primitive quantity.

Points whose excluded IPP falls outside the coverage radius of the remaining
stations (`TecMapConfig.ipp_gradient_radius_km`) are flagged `in_coverage=False`
and excluded from the headline metrics: the rendered map masks those areas out,
so errors there do not describe anything a user can see.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from scipy.interpolate import griddata

from app.tec_map_kriging import (
    MIN_POINTS_FOR_KRIGING,
    _haversine_km,
    fit_exponential_variogram,
    kriging_interpolate,
    pairwise_distances_km,
)
from app.tec_map_pipeline import TecMapConfig

logger = logging.getLogger(__name__)

# A frame needs at least this many stations for a leave-one-out split to leave
# a meaningful training set (3 points = one Delaunay triangle) behind.
MIN_STATIONS_FOR_LOSO = 4

CV_POINT_COLUMNS = [
    "frame_time",
    "station",
    "ipp_lon",
    "ipp_lat",
    "vtec_obs",
    "vtec_pred",
    "error",
    "n_train",
    "in_coverage",
]


def predict_at_points(
    train_lon: np.ndarray,
    train_lat: np.ndarray,
    train_values: np.ndarray,
    target_lon: np.ndarray,
    target_lat: np.ndarray,
    pipeline: TecMapConfig,
    variogram: tuple[float, float, float] | None = None,
) -> np.ndarray:
    """
    Predict the field at scattered target points — the same interpolation
    dispatch as `interpolate_frame`, but for points instead of a mesh.
    """
    train_lon = np.asarray(train_lon, dtype=float)
    train_lat = np.asarray(train_lat, dtype=float)
    train_values = np.asarray(train_values, dtype=float)
    target_lon = np.asarray(target_lon, dtype=float)
    target_lat = np.asarray(target_lat, dtype=float)

    method = str(pipeline.interpolation_method or "linear").strip().lower()
    if method == "kriging" and len(train_values) >= MIN_POINTS_FOR_KRIGING:
        return np.asarray(
            kriging_interpolate(train_lon, train_lat, train_values, target_lon, target_lat, variogram=variogram),
            dtype=float,
        )

    train_points = np.column_stack([train_lon, train_lat])
    if len(train_values) >= 3:
        griddata_method = "linear" if method == "kriging" else pipeline.interpolation_method
        primary = griddata(train_points, train_values, (target_lon, target_lat), method=griddata_method)
        fallback = griddata(
            train_points, train_values, (target_lon, target_lat), method=pipeline.fallback_interpolation_method
        )
        return np.asarray(np.where(np.isnan(primary), fallback, primary), dtype=float)
    return np.asarray(
        griddata(train_points, train_values, (target_lon, target_lat), method=pipeline.fallback_interpolation_method),
        dtype=float,
    )


def loso_frame(frame: pd.DataFrame, pipeline: TecMapConfig) -> pd.DataFrame:
    """
    Leave-one-station-out cross-validation for a single frame.

    Returns one row per station: observed vs predicted VTEC at the excluded
    station's IPP, the training-set size and whether the excluded point stays
    inside the coverage radius of the remaining IPPs.
    """
    n = len(frame)
    if n < MIN_STATIONS_FOR_LOSO:
        return pd.DataFrame(columns=[c for c in CV_POINT_COLUMNS if c != "frame_time"])

    lon = frame["ipp_lon"].to_numpy(dtype=float)
    lat = frame["ipp_lat"].to_numpy(dtype=float)
    values = frame["vtec_tecu"].to_numpy(dtype=float)
    stations = frame["station"].astype(str).to_numpy()

    method = str(pipeline.interpolation_method or "linear").strip().lower()
    variogram: tuple[float, float, float] | None = None
    if method == "kriging" and n - 1 >= MIN_POINTS_FOR_KRIGING:
        # Fit the variogram once on the full frame; each leave-one-out subset
        # reuses it (standard CV practice — the model, not the data, is fixed).
        variogram = fit_exponential_variogram(pairwise_distances_km(lon, lat), values)

    coverage_radius_km = pipeline.ipp_gradient_radius_km
    lon_rad = np.deg2rad(lon)
    lat_rad = np.deg2rad(lat)

    rows: list[dict] = []
    for i in range(n):
        keep = np.arange(n) != i
        predicted = float(
            predict_at_points(
                lon[keep], lat[keep], values[keep],
                np.array([lon[i]]), np.array([lat[i]]),
                pipeline,
                variogram=variogram,
            )[0]
        )
        if coverage_radius_km is None:
            in_coverage = True
        else:
            distances = _haversine_km(lon_rad[i], lat_rad[i], lon_rad[keep], lat_rad[keep])
            in_coverage = bool(np.min(distances) <= float(coverage_radius_km))
        rows.append(
            {
                "station": stations[i],
                "ipp_lon": float(lon[i]),
                "ipp_lat": float(lat[i]),
                "vtec_obs": float(values[i]),
                "vtec_pred": predicted,
                "error": predicted - float(values[i]),
                "n_train": int(n - 1),
                "in_coverage": in_coverage,
            }
        )
    return pd.DataFrame(rows)


def loso_cross_validate(frame_summary: pd.DataFrame, pipeline: TecMapConfig) -> pd.DataFrame:
    """
    LOSO cross-validation over every frame of a frame summary.

    Frames with fewer than MIN_STATIONS_FOR_LOSO stations are skipped. Returns
    a per-point DataFrame with CV_POINT_COLUMNS.
    """
    if frame_summary.empty:
        return pd.DataFrame(columns=CV_POINT_COLUMNS)

    parts: list[pd.DataFrame] = []
    for frame_time, frame in frame_summary.groupby("frame_time", sort=True):
        frame_cv = loso_frame(frame, pipeline)
        if frame_cv.empty:
            continue
        frame_cv.insert(0, "frame_time", pd.Timestamp(frame_time))
        parts.append(frame_cv)

    if not parts:
        return pd.DataFrame(columns=CV_POINT_COLUMNS)
    return pd.concat(parts, ignore_index=True)


def _error_metrics(errors: np.ndarray) -> dict:
    errors = np.asarray(errors, dtype=float)
    errors = errors[np.isfinite(errors)]
    if errors.size == 0:
        return {"n": 0, "bias_tecu": None, "mae_tecu": None, "rmse_tecu": None}
    return {
        "n": int(errors.size),
        "bias_tecu": float(np.mean(errors)),
        "mae_tecu": float(np.mean(np.abs(errors))),
        "rmse_tecu": float(np.sqrt(np.mean(errors**2))),
    }


def summarize_validation(cv: pd.DataFrame) -> dict:
    """
    Aggregate per-point LOSO results into overall / per-station / per-frame
    metrics. Headline numbers use only in-coverage points; out-of-coverage
    points are counted separately.
    """
    if cv.empty:
        return {
            "overall": {**_error_metrics(np.array([])), "n_out_of_coverage": 0},
            "per_station": [],
            "per_frame": [],
        }

    in_cov = cv[cv["in_coverage"]]
    overall = _error_metrics(in_cov["error"].to_numpy())
    overall["n_out_of_coverage"] = int((~cv["in_coverage"]).sum())

    per_station = []
    for station, group in cv.groupby("station", sort=True):
        metrics = _error_metrics(group.loc[group["in_coverage"], "error"].to_numpy())
        metrics["station"] = str(station)
        metrics["n_out_of_coverage"] = int((~group["in_coverage"]).sum())
        per_station.append(metrics)

    per_frame = []
    for frame_time, group in cv.groupby("frame_time", sort=True):
        metrics = _error_metrics(group.loc[group["in_coverage"], "error"].to_numpy())
        per_frame.append({"frame_time": pd.Timestamp(frame_time).isoformat(), **metrics})

    return {"overall": overall, "per_station": per_station, "per_frame": per_frame}


def frame_accuracy_label(frame: pd.DataFrame, pipeline: TecMapConfig) -> str | None:
    """
    Short per-frame accuracy annotation for rendered maps, e.g.
    "LOSO RMSE 0.82 TECU (n=9)". None when the frame is too small for LOSO
    or no excluded point stays inside the coverage of the others.
    """
    cv = loso_frame(frame, pipeline)
    if cv.empty:
        return None
    metrics = _error_metrics(cv.loc[cv["in_coverage"], "error"].to_numpy())
    if metrics["n"] == 0:
        return None
    return f"LOSO RMSE {metrics['rmse_tecu']:.2f} TECU (n={metrics['n']})"
