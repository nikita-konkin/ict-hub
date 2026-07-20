"""
tec_map_render.py — Rendering utilities for the TEC map pipeline (ict-hub).

Outputs:
  - Animated GIF bytes for a frame range
  - Plotly Figure JSON (sanitized) for a single frame
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import json
import logging
import math
import os
import tempfile
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
from PIL import GifImagePlugin, Image
from scipy.interpolate import griddata
from scipy.ndimage import gaussian_filter, zoom

import matplotlib

matplotlib.use("Agg")  # headless/server rendering
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.colors import LinearSegmentedColormap


def _configure_map_fonts() -> None:
    """Times New Roman for all map text (bundled TTFs cover the container)."""
    fonts_dir = Path(__file__).resolve().parent / "fonts"
    if fonts_dir.is_dir():
        for ttf in fonts_dir.glob("*.ttf"):
            try:
                font_manager.fontManager.addfont(str(ttf))
            except Exception:
                logger.warning("failed to register font %s", ttf)
    matplotlib.rcParams["font.family"] = "serif"
    matplotlib.rcParams["font.serif"] = ["Times New Roman", "Liberation Serif", "DejaVu Serif"]
    matplotlib.rcParams["font.size"] = 12.0  # default 10 + 2 pt


_configure_map_fonts()

PLOTLY_MAP_FONT_FAMILY = "Times New Roman, Liberation Serif, serif"

import plotly.graph_objects as go
from _plotly_utils.utils import PlotlyJSONEncoder

from app.tec_map_pipeline import TecMapConfig
from app.tec_map_kriging import MIN_POINTS_FOR_KRIGING, kriging_interpolate
from app.tec_map_lpi import MIN_POINTS_FOR_LPI, lpi_interpolate
from app.tec_map_validation import frame_accuracy_label
from app.tec_map_fields import (
    compute_bk_grid,
    compute_gdd_grid,
    resolve_signal_band,
    signal_band_label,
)
from app.tec_map_iri import iri_vtec_grid_for_frames


logger = logging.getLogger(__name__)

TILE_SIZE_PX = 256
WEB_MERCATOR_MAX_LAT_DEG = 85.05112878
WEB_MERCATOR_RADIUS_M = 6378137.0
TEC_MAP_COLOR_STOPS: list[tuple[float, str]] = [
    (0.00, "#0000ff"),
    (0.20, "#1e90ff"),
    (0.40, "#00e5ff"),
    (0.58, "#00ff66"),
    (0.76, "#ffff00"),
    (0.90, "#ff9900"),
    (1.00, "#ff0000"),
]
TEC_MAP_MATPLOTLIB_CMAP = LinearSegmentedColormap.from_list(
    "tec_map_spectrum",
    [color for _, color in TEC_MAP_COLOR_STOPS],
    N=256,
)
TEC_MAP_PLOTLY_COLORSCALE = [[stop, color] for stop, color in TEC_MAP_COLOR_STOPS]

# ~111.32 km per degree of latitude (mean Earth radius approximation).
KM_PER_DEGREE_LAT = 111.32


@dataclass(frozen=True, slots=True)
class TecMapRenderConfig:
    contour_levels: int = 128
    label_station_markers: bool = True
    label_ipp_samples: bool = False

    # Which scalar field to render. Supported:
    #   "vtec"          — VTEC magnitude [TECU] (default)
    #   "vtec_gradient" — |∇VTEC| spatial-gradient magnitude [TECU / 100 km]
    #   "gdd"           — group delay dispersion magnitude |D| [ns/GHz] at `signal_band`
    #   "b_k"           — coherence bandwidth B_k [MHz] at `signal_band`
    field: str = "vtec"
    # Carrier band for the derived propagation fields (see tec_map_fields).
    signal_band: str = "gps_l1"

    basemap_enabled: bool = False
    basemap_mode: str = "off"
    basemap_zoom: int | None = None
    basemap_max_tiles: int = 16
    basemap_alpha: float = 0.28
    vtec_layer_alpha: float | None = None

    frame_dpi: int = 120
    gif_frame_duration_seconds: float = 0.8

    # Animation container: "gif" (default), "mp4" (H.264) or "webm" (VP9).
    # Video formats avoid the 256-colour GIF palette entirely.
    animation_format: str = "gif"
    # True → GIF frames use an adaptive MEDIANCUT palette with Floyd–Steinberg
    # dithering (smoother gradients, slower encode) instead of FASTOCTREE.
    gif_high_quality: bool = False
    # Bilinear grid upsampling factor applied before contouring (1 = off).
    # Smooths contour edges and the coverage-mask boundary at high DPI.
    upsample_factor: int = 1
    # Explicit colour-scale overrides. When set they replace the corresponding
    # quantile-based limit, keeping figures comparable across requests.
    color_min: float | None = None
    color_max: float | None = None
    # Annotate every rendered frame with its leave-one-station-out accuracy
    # ("LOSO RMSE … TECU"). Adds one LOSO pass per frame at render time.
    show_accuracy: bool = False
    # Print the map-model parameters (grid step, smoothing, frame length,
    # ionosphere height, elevation cutoff, coverage radius, interpolation)
    # as a caption line under the map.
    show_params: bool = False

    # IRI reference-model comparison: "off" (default), "iri" (render the IRI
    # climatological field itself, full extent — model validity is not limited
    # by station coverage) or "difference" (empirical − IRI inside the
    # empirical coverage mask, diverging colour scale, bias/RMSE annotation).
    model_mode: str = "off"
    # Explicit daily adjusted F10.7 for the IRI evaluation; None → automatic
    # (spaceweather.gc.ca flux table with disk cache and default fallback).
    f107_override: float | None = None

    # Basemap cache root. If None, cache-backed basemap modes are unavailable.
    basemap_cache_root: Path | None = None
    # HTTP XYZ tile-server URL template with {z}/{x}/{y} placeholders
    # (e.g. "http://localhost:8090/tile/{z}/{x}/{y}.png"). If None, tile_server mode is unavailable.
    basemap_tile_server_url: str | None = None
    # If True, fall back to plain lat/lon rendering when the requested basemap source is unavailable.
    basemap_fallback_to_plain: bool = True


@dataclass(slots=True)
class BasemapLayer:
    image: np.ndarray
    extent: tuple[float, float, float, float]
    attribution: str


# ---------------------------------------------------------------------------
# Gridding / smoothing (ported from prototype)
# ---------------------------------------------------------------------------

def smooth_grid(grid: np.ndarray, sigma: float) -> np.ndarray:
    if sigma <= 0 or not np.isfinite(grid).any():
        return grid

    valid_mask = np.isfinite(grid)
    if valid_mask.all():
        return gaussian_filter(grid, sigma=sigma, mode="nearest")

    filled_grid = np.where(valid_mask, grid, 0.0)
    smoothed_values = gaussian_filter(filled_grid, sigma=sigma, mode="constant", cval=0.0)
    smoothed_weights = gaussian_filter(valid_mask.astype(float), sigma=sigma, mode="constant", cval=0.0)

    with np.errstate(divide="ignore", invalid="ignore"):
        smoothed_grid = smoothed_values / smoothed_weights
    smoothed_grid[smoothed_weights <= 0] = np.nan
    return smoothed_grid


def great_circle_distance_km(
    grid_lon: np.ndarray,
    grid_lat: np.ndarray,
    point_lon: np.ndarray,
    point_lat: np.ndarray,
) -> np.ndarray:
    earth_radius_km = 6371.0
    grid_lon_rad = np.deg2rad(np.asarray(grid_lon, dtype=float))[..., np.newaxis]
    grid_lat_rad = np.deg2rad(np.asarray(grid_lat, dtype=float))[..., np.newaxis]
    point_lon_rad = np.deg2rad(np.asarray(point_lon, dtype=float)).reshape(1, 1, -1)
    point_lat_rad = np.deg2rad(np.asarray(point_lat, dtype=float)).reshape(1, 1, -1)

    delta_lat = point_lat_rad - grid_lat_rad
    delta_lon = (point_lon_rad - grid_lon_rad + np.pi) % (2.0 * np.pi) - np.pi
    haversine = (
        np.sin(delta_lat / 2.0) ** 2
        + np.cos(grid_lat_rad) * np.cos(point_lat_rad) * np.sin(delta_lon / 2.0) ** 2
    )
    central_angle = 2.0 * np.arcsin(np.sqrt(np.clip(haversine, 0.0, 1.0)))
    return earth_radius_km * central_angle


def ipp_coverage_mask(
    frame: pd.DataFrame,
    grid_lon: np.ndarray,
    grid_lat: np.ndarray,
    pipeline: TecMapConfig,
) -> np.ndarray:
    if pipeline.ipp_gradient_radius_km is None:
        return np.ones(grid_lon.shape, dtype=bool)
    if frame.empty:
        return np.zeros(grid_lon.shape, dtype=bool)

    distances = great_circle_distance_km(
        grid_lon,
        grid_lat,
        frame["ipp_lon"].to_numpy(),
        frame["ipp_lat"].to_numpy(),
    )
    return np.min(distances, axis=-1) <= float(pipeline.ipp_gradient_radius_km)


def soften_coverage_mask(mask: np.ndarray, pipeline: TecMapConfig, scale: float = 1.0) -> np.ndarray:
    mask_bool = np.asarray(mask, dtype=bool)
    sigma_cells = float(getattr(pipeline, "coverage_mask_smoothing_cells", 0.0) or 0.0) * max(float(scale), 1.0)
    if sigma_cells <= 0 or mask_bool.size == 0:
        return mask_bool

    if mask_bool.all() or not mask_bool.any():
        return mask_bool

    softened = gaussian_filter(mask_bool.astype(float), sigma=sigma_cells, mode="nearest")
    return softened >= 0.5


def compute_vtec_gradient_magnitude(
    grid: np.ndarray,
    grid_lon: np.ndarray,
    grid_lat: np.ndarray,
) -> np.ndarray:
    """
    |∇VTEC| in TECU per 100 km on a regular lon/lat grid.

    The longitudinal step shrinks as cos(lat), so the east-west derivative is
    rescaled per row before combining with the (uniform) north-south derivative.
    NaN cells propagate; edge cells use one-sided differences via np.gradient.
    """
    if grid.ndim != 2 or grid.shape[0] < 2 or grid.shape[1] < 2:
        return np.full(grid.shape, np.nan, dtype=float)

    dlat_deg = float(np.abs(grid_lat[1, 0] - grid_lat[0, 0]))
    dlon_deg = float(np.abs(grid_lon[0, 1] - grid_lon[0, 0]))
    if dlat_deg <= 0 or dlon_deg <= 0:
        return np.full(grid.shape, np.nan, dtype=float)

    dlat_km = dlat_deg * KM_PER_DEGREE_LAT

    d_dy = np.gradient(grid, dlat_km, axis=0)        # TECU/km, lat direction
    d_dx_per_deg = np.gradient(grid, dlon_deg, axis=1)  # TECU per degree-of-longitude

    cos_lat = np.cos(np.deg2rad(grid_lat))
    cos_lat = np.where(np.abs(cos_lat) < 1e-6, 1e-6, cos_lat)
    d_dx = d_dx_per_deg / (KM_PER_DEGREE_LAT * cos_lat)  # TECU/km, lon direction

    magnitude_per_km = np.sqrt(d_dx ** 2 + d_dy ** 2)
    return magnitude_per_km * 100.0  # TECU per 100 km


def _field_render_spec(field: str, signal_band: str = "gps_l1") -> dict[str, Any]:
    """
    Per-field rendering metadata and transforms.

    Keys:
      matplotlib_cmap / plotly_colorscale / colorbar_label / title_prefix / hover_unit
      vmin_floor      — fixed lower colour limit for derived fields (None → quantile)
      grid_transform  — callable(grid, grid_lon, grid_lat) -> derived grid, or None
      point_transform — callable(vtec_values) -> per-IPP values on the field's colour
                        scale, or None when per-point values are not comparable
                        (IPP markers are then drawn as neutral position-only dots)
      derived_scale   — True → colour limits come from the transformed grids
                        (VTEC keeps its 5–95% quantiles over raw samples)
    """
    name = (field or "vtec").strip().lower()
    if name == "vtec_gradient":
        return {
            "matplotlib_cmap": "magma",
            "plotly_colorscale": "Magma",
            "colorbar_label": "|∇VTEC| [TECU / 100 km]",
            "title_prefix": "|∇VTEC| map",
            "hover_unit": "TECU/100km",
            "vmin_floor": 0.0,
            "grid_transform": compute_vtec_gradient_magnitude,
            "point_transform": None,
            "derived_scale": True,
        }
    if name == "gdd":
        band, f_hz = resolve_signal_band(signal_band)
        band_label = signal_band_label(band)
        return {
            "matplotlib_cmap": "inferno",
            "plotly_colorscale": "Inferno",
            "colorbar_label": f"|D| [ns/GHz] — {band_label}",
            "title_prefix": f"GDD map ({band_label})",
            "hover_unit": "ns/GHz",
            "vmin_floor": 0.0,
            "grid_transform": lambda grid, grid_lon, grid_lat: compute_gdd_grid(grid, f_hz),
            "point_transform": lambda values: compute_gdd_grid(np.asarray(values, dtype=float), f_hz),
            "derived_scale": True,
        }
    if name == "b_k":
        band, f_hz = resolve_signal_band(signal_band)
        band_label = signal_band_label(band)
        return {
            "matplotlib_cmap": "viridis",
            "plotly_colorscale": "Viridis",
            "colorbar_label": f"B_k [MHz] — {band_label}",
            "title_prefix": f"B_k map ({band_label})",
            "hover_unit": "MHz",
            "vmin_floor": None,
            "grid_transform": lambda grid, grid_lon, grid_lat: compute_bk_grid(grid, f_hz),
            "point_transform": lambda values: compute_bk_grid(np.asarray(values, dtype=float), f_hz),
            "derived_scale": True,
        }
    return {
        "matplotlib_cmap": TEC_MAP_MATPLOTLIB_CMAP,
        "plotly_colorscale": TEC_MAP_PLOTLY_COLORSCALE,
        "colorbar_label": "VTEC [TECU]",
        "title_prefix": "VTEC map",
        "hover_unit": "TECU",
        "vmin_floor": None,
        "grid_transform": None,
        "point_transform": lambda values: np.asarray(values, dtype=float),
        "derived_scale": False,
    }


def interpolate_frame(
    frame: pd.DataFrame,
    grid_lon: np.ndarray,
    grid_lat: np.ndarray,
    pipeline: TecMapConfig,
    coverage_mask: np.ndarray | None = None,
) -> np.ndarray:
    points = frame[["ipp_lon", "ipp_lat"]].to_numpy()
    values = frame["vtec_tecu"].to_numpy()

    method = str(pipeline.interpolation_method or "linear").strip().lower()
    if method == "kriging" and len(frame) >= MIN_POINTS_FOR_KRIGING:
        interpolated = kriging_interpolate(
            frame["ipp_lon"].to_numpy(),
            frame["ipp_lat"].to_numpy(),
            values,
            grid_lon,
            grid_lat,
        )
    elif method == "lpi" and len(frame) >= MIN_POINTS_FOR_LPI:
        interpolated = lpi_interpolate(
            frame["ipp_lon"].to_numpy(),
            frame["ipp_lat"].to_numpy(),
            values,
            grid_lon,
            grid_lat,
            degree=int(getattr(pipeline, "lpi_degree", 1)),
        )
    else:
        # Delaunay-linear with nearest fill (also the small-frame fallback for kriging/LPI).
        griddata_method = "linear" if method in {"kriging", "lpi"} else pipeline.interpolation_method
        if len(frame) >= 3:
            primary = griddata(points, values, (grid_lon, grid_lat), method=griddata_method)
            fallback = griddata(points, values, (grid_lon, grid_lat), method=pipeline.fallback_interpolation_method)
            interpolated = np.where(np.isnan(primary), fallback, primary)
        else:
            interpolated = griddata(points, values, (grid_lon, grid_lat), method=pipeline.fallback_interpolation_method)

    if coverage_mask is None:
        coverage_mask = ipp_coverage_mask(frame, grid_lon, grid_lat, pipeline)
    return np.where(coverage_mask, interpolated, np.nan)


def upsample_grid(grid: np.ndarray, factor: int) -> np.ndarray:
    """
    Bilinear upsampling of a lon/lat grid that preserves the NaN coverage mask.

    Values are zoomed with zero-filled NaNs alongside a validity weight, then
    renormalized (same trick as smooth_grid) so coverage edges do not darken.
    Cells whose interpolated validity falls below 0.5 stay NaN.
    """
    factor = int(factor)
    if factor <= 1 or grid.ndim != 2:
        return grid

    valid = np.isfinite(grid)
    filled = np.where(valid, grid, 0.0)
    zoomed_values = zoom(filled, factor, order=1, mode="nearest")
    zoomed_weight = zoom(valid.astype(float), factor, order=1, mode="nearest")

    with np.errstate(divide="ignore", invalid="ignore"):
        upsampled = zoomed_values / zoomed_weight
    upsampled[zoomed_weight < 0.5] = np.nan
    return upsampled


def upsample_coordinates(grid_lon: np.ndarray, grid_lat: np.ndarray, factor: int) -> tuple[np.ndarray, np.ndarray]:
    """Zoom coordinate meshes to match upsample_grid output (linear → exact)."""
    factor = int(factor)
    if factor <= 1:
        return grid_lon, grid_lat
    return (
        zoom(np.asarray(grid_lon, dtype=float), factor, order=1, mode="nearest"),
        zoom(np.asarray(grid_lat, dtype=float), factor, order=1, mode="nearest"),
    )


def compute_field_grid(
    frame: pd.DataFrame,
    grid_lon: np.ndarray,
    grid_lat: np.ndarray,
    pipeline: TecMapConfig,
    grid_transform: Any = None,
    *,
    upsample_factor: int = 1,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Full per-frame grid pipeline. Returns (grid, plot_lon, plot_lat) at render
    resolution (base grid × upsample_factor).

    Order matters for edge quality: the field is interpolated, smoothed and
    (optionally) transformed on the base grid *without* the coverage cut, then
    bilinearly upsampled, and only then clipped by the exact great-circle
    coverage mask evaluated at render resolution. Evaluating the mask on the
    fine grid keeps the boundary a smooth union of 300-km circles instead of
    the stair-stepped outline of coarse grid cells.
    """
    no_cut = np.ones(grid_lon.shape, dtype=bool)
    grid = interpolate_frame(frame, grid_lon, grid_lat, pipeline, coverage_mask=no_cut)
    grid = smooth_grid(grid, pipeline.smoothing_sigma)
    if pipeline.enforce_nonnegative_vtec:
        grid = np.where(np.isfinite(grid), np.maximum(grid, 0.0), grid)
    if grid_transform is not None:
        grid = grid_transform(grid, grid_lon, grid_lat)

    grid = upsample_grid(grid, upsample_factor)
    plot_lon, plot_lat = upsample_coordinates(grid_lon, grid_lat, upsample_factor)

    coverage_mask = ipp_coverage_mask(frame, plot_lon, plot_lat, pipeline)
    coverage_mask = soften_coverage_mask(coverage_mask, pipeline, scale=float(upsample_factor))
    grid = np.where(coverage_mask, grid, np.nan)
    return grid, plot_lon, plot_lat


def _derived_color_limits(field_spec: dict[str, Any], grids: list[np.ndarray]) -> tuple[float, float]:
    """Colour limits for derived fields: vmin_floor (or 5% quantile) → 95% quantile."""
    finite_values: list[np.ndarray] = []
    for grid in grids:
        if np.isfinite(grid).any():
            finite_values.append(grid[np.isfinite(grid)].ravel())
    if not finite_values:
        return 0.0, 1.0

    stacked = np.concatenate(finite_values)
    vmax = float(np.quantile(stacked, 0.95))
    if not np.isfinite(vmax) or vmax <= 0.0:
        vmax = float(np.nanmax(stacked)) if stacked.size else 1.0
    vmin_floor = field_spec.get("vmin_floor")
    vmin = float(vmin_floor) if vmin_floor is not None else float(np.quantile(stacked, 0.05))
    if vmin >= vmax:
        vmin = float(vmin_floor) if vmin_floor is not None else 0.0
    if math.isclose(vmin, vmax):
        vmax = vmin + 1.0
    return vmin, vmax


def _vtec_color_limits(frame_summary: pd.DataFrame, pipeline: TecMapConfig) -> tuple[float, float]:
    """Colour limits for raw VTEC: 5–95% quantile over the selection's samples."""
    vmin, vmax = np.quantile(frame_summary["vtec_tecu"], [0.05, 0.95])
    vmin, vmax = float(vmin), float(vmax)
    if math.isclose(vmin, vmax):
        vmin -= 1.0
        vmax += 1.0
    if pipeline.enforce_nonnegative_vtec:
        vmin = max(vmin, 0.0)
    return vmin, vmax


def _apply_color_overrides(vmin: float, vmax: float, render: TecMapRenderConfig) -> tuple[float, float]:
    if render.color_min is not None:
        vmin = float(render.color_min)
    if render.color_max is not None:
        vmax = float(render.color_max)
    if vmin >= vmax:
        raise ValueError(f"Invalid colour limits: color_min={vmin} must be below color_max={vmax}.")
    return vmin, vmax


# ---------------------------------------------------------------------------
# IRI reference-model comparison (render.model_mode = "iri" | "difference")
# ---------------------------------------------------------------------------

def model_field_grids(
    frame_times: list[pd.Timestamp],
    plot_lon: np.ndarray,
    plot_lat: np.ndarray,
    field_spec: dict[str, Any],
    render: TecMapRenderConfig,
) -> tuple[dict[pd.Timestamp, np.ndarray], dict[str, dict[str, float | str]]]:
    """IRI model grids converted into the rendered field's units."""
    raw_grids, f107_meta = iri_vtec_grid_for_frames(frame_times, plot_lon, plot_lat, render.f107_override)
    transform = field_spec.get("grid_transform")
    if transform is not None:
        raw_grids = {ts: transform(grid, plot_lon, plot_lat) for ts, grid in raw_grids.items()}
    return raw_grids, f107_meta


def model_render_spec(field_spec: dict[str, Any], model_mode: str) -> dict[str, Any]:
    """Adjust a field render spec for the requested model mode."""
    spec = dict(field_spec)
    if model_mode == "iri":
        spec["title_prefix"] = f"{field_spec['title_prefix']} — IRI model"
        # Empirical per-IPP values live on the *relative* VTEC scale and sit
        # far below the absolute model level — colouring them on the model
        # scale would just clip them at the bottom. Neutral markers instead.
        spec["point_transform"] = None
        return spec
    if model_mode == "difference":
        spec["matplotlib_cmap"] = "RdBu_r"
        spec["plotly_colorscale"] = "RdBu_r"
        spec["colorbar_label"] = f"Δ {field_spec['colorbar_label']} (obs − IRI)"
        spec["title_prefix"] = f"{field_spec['title_prefix']} — obs − IRI"
        spec["vmin_floor"] = None
        # Per-IPP values are absolute field values, not differences — off scale.
        spec["point_transform"] = None
        spec["derived_scale"] = True
    return spec


def model_color_limits(grids: list[np.ndarray]) -> tuple[float, float]:
    """Full finite min–max: the model field is smooth, quantile clipping only
    creates artificial saturated regions."""
    finite_values = [grid[np.isfinite(grid)].ravel() for grid in grids if np.isfinite(grid).any()]
    if not finite_values:
        return 0.0, 1.0
    stacked = np.concatenate(finite_values)
    vmin, vmax = float(stacked.min()), float(stacked.max())
    if math.isclose(vmin, vmax):
        vmin -= 1.0
        vmax += 1.0
    return vmin, vmax


def difference_color_limits(grids: list[np.ndarray]) -> tuple[float, float]:
    """Symmetric colour limits around zero from the 98% quantile of |Δ|."""
    finite_values = [grid[np.isfinite(grid)].ravel() for grid in grids if np.isfinite(grid).any()]
    if not finite_values:
        return -1.0, 1.0
    stacked = np.abs(np.concatenate(finite_values))
    limit = float(np.quantile(stacked, 0.98))
    if not np.isfinite(limit) or limit <= 0.0:
        limit = float(stacked.max()) if stacked.size and stacked.max() > 0 else 1.0
    return -limit, limit


def model_difference_label(diff_grid: np.ndarray, unit: str) -> str | None:
    """Per-frame difference statistics: 'obs − IRI: bias …, RMSE … <unit>'."""
    values = diff_grid[np.isfinite(diff_grid)]
    if values.size == 0:
        return None
    bias = float(values.mean())
    rmse = float(np.sqrt(np.mean(values**2)))
    return f"obs − IRI: bias {bias:+.2f}, RMSE {rmse:.2f} {unit}".rstrip()


def _f107_caption(f107_meta: dict[str, dict[str, float | str]]) -> str:
    return ", ".join(f"{day}: {meta['f107']} ({meta['source']})" for day, meta in sorted(f107_meta.items()))


# ---------------------------------------------------------------------------
# Basemap helpers (OpenStreetMap tiles; optional)
# ---------------------------------------------------------------------------

def choose_tile_zoom(bounds: tuple[float, float, float, float], max_tiles: int) -> int:
    lon_min, lon_max, lat_min, lat_max = bounds
    if max_tiles <= 0:
        return 3

    for zoom in range(12, 2, -1):
        x_start_f, y_top_f = lonlat_to_tile_fraction(lon_min, lat_max, zoom)
        x_end_f, y_bottom_f = lonlat_to_tile_fraction(lon_max, lat_min, zoom)
        cols = math.floor(x_end_f) - math.floor(x_start_f) + 1
        rows = math.floor(y_bottom_f) - math.floor(y_top_f) + 1
        if cols <= 0 or rows <= 0:
            continue
        if cols * rows <= max_tiles:
            return zoom
    return 3


def lonlat_to_tile_fraction(lon_deg: float, lat_deg: float, zoom: int) -> tuple[float, float]:
    lat_deg = max(min(lat_deg, WEB_MERCATOR_MAX_LAT_DEG), -WEB_MERCATOR_MAX_LAT_DEG)
    lat_rad = math.radians(lat_deg)
    n = 2**zoom
    x = (lon_deg + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n
    return x, y


def tile_fraction_to_lonlat(x: float, y: float, zoom: int) -> tuple[float, float]:
    n = 2**zoom
    lon_deg = x / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / n)))
    lat_deg = math.degrees(lat_rad)
    return lon_deg, lat_deg


def lonlat_to_web_mercator(
    lon_deg: float | np.ndarray,
    lat_deg: float | np.ndarray,
) -> tuple[float | np.ndarray, float | np.ndarray]:
    lon_array = np.asarray(lon_deg, dtype=float)
    lat_array = np.asarray(lat_deg, dtype=float)
    lat_array = np.clip(lat_array, -WEB_MERCATOR_MAX_LAT_DEG, WEB_MERCATOR_MAX_LAT_DEG)

    x_m = WEB_MERCATOR_RADIUS_M * np.deg2rad(lon_array)
    y_m = WEB_MERCATOR_RADIUS_M * np.log(np.tan(np.pi / 4.0 + np.deg2rad(lat_array) / 2.0))

    if x_m.ndim == 0:
        return float(x_m), float(y_m)
    return x_m, y_m


def _open_cached_tile(tile_path: Path) -> Image.Image:
    return Image.open(tile_path).convert("RGBA")


def _find_xyz_tile_path(root: Path, x: int, y: int, zoom: int) -> Path | None:
    wrapped_x = x % (2**zoom)
    for extension in (".png", ".jpg", ".jpeg", ".webp"):
        candidate = root / str(zoom) / str(wrapped_x) / f"{y}{extension}"
        if candidate.exists():
            return candidate
    return None


def fetch_cached_xyz_tile(x: int, y: int, zoom: int, tiles_root: Path) -> Image.Image:
    tile_path = _find_xyz_tile_path(tiles_root, x, y, zoom)
    if tile_path is None:
        raise FileNotFoundError(f"XYZ tile not found for z={zoom}, x={x}, y={y} under {tiles_root}.")
    return _open_cached_tile(tile_path)


def fetch_tile_server_tile(x: int, y: int, zoom: int, url_template: str) -> Image.Image:
    wrapped_x = x % (2**zoom)
    url = url_template.format(z=zoom, x=wrapped_x, y=y)
    request = Request(url, headers={"User-Agent": "ict-hub/tec-map"})
    with urlopen(request, timeout=15) as response:
        data = response.read()
    return Image.open(BytesIO(data)).convert("RGBA")


def fetch_openstreetmap_tile(
    x: int,
    y: int,
    zoom: int,
    cache_root: Path,
    *,
    allow_network: bool,
) -> Image.Image:
    wrapped_x = x % (2**zoom)
    cache_path = cache_root / "openstreetmap" / str(zoom) / str(wrapped_x) / f"{y}.png"
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    if cache_path.exists():
        return _open_cached_tile(cache_path)

    if not allow_network:
        raise FileNotFoundError(
            f"Cached OpenStreetMap tile not found for z={zoom}, x={wrapped_x}, y={y} under {cache_root}."
        )

    url = f"https://tile.openstreetmap.org/{zoom}/{wrapped_x}/{y}.png"
    request = Request(url, headers={"User-Agent": "ict-hub/tec-map"})
    with urlopen(request, timeout=15) as response:
        data = response.read()
    cache_path.write_bytes(data)
    return Image.open(BytesIO(data)).convert("RGBA")


def fetch_xyz_layer_at_zoom(
    bounds: tuple[float, float, float, float],
    *,
    tile_fetcher: Any,
    zoom: int,
    max_tiles: int,
    attribution: str,
) -> BasemapLayer:
    lon_min, lon_max, lat_min, lat_max = bounds

    x_start_f, y_top_f = lonlat_to_tile_fraction(lon_min, lat_max, zoom)
    x_end_f, y_bottom_f = lonlat_to_tile_fraction(lon_max, lat_min, zoom)

    x_start = math.floor(x_start_f)
    x_end = math.floor(x_end_f)
    y_start = math.floor(y_top_f)
    y_end = math.floor(y_bottom_f)

    cols = x_end - x_start + 1
    rows = y_end - y_start + 1
    if cols <= 0 or rows <= 0 or cols * rows > max_tiles:
        raise RuntimeError(f"Refusing basemap tile grid {cols}x{rows} at zoom={zoom} (max_tiles={max_tiles}).")

    coords: list[tuple[int, int]] = []
    for y in range(y_start, y_end + 1):
        for x in range(x_start, x_end + 1):
            coords.append((x, y))

    # Parallel tile fetch: reduces wall-clock time on first load (cache cold).
    # Keep worker count modest to avoid overwhelming the tile server.
    max_workers = min(8, max(2, len(coords)))
    fetched: dict[tuple[int, int], Image.Image] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(tile_fetcher, x, y, zoom): (x, y) for (x, y) in coords}
        for fut in as_completed(futures):
            x, y = futures[fut]
            fetched[(x, y)] = fut.result()

    tile_images: list[list[Image.Image]] = []
    for y in range(y_start, y_end + 1):
        row: list[Image.Image] = []
        for x in range(x_start, x_end + 1):
            row.append(fetched[(x, y)])
        tile_images.append(row)

    stitched = Image.new("RGBA", (cols * TILE_SIZE_PX, rows * TILE_SIZE_PX))
    for row_idx, row in enumerate(tile_images):
        for col_idx, tile in enumerate(row):
            stitched.paste(tile, (col_idx * TILE_SIZE_PX, row_idx * TILE_SIZE_PX))

    lon_left, lat_top = tile_fraction_to_lonlat(float(x_start), float(y_start), zoom)
    lon_right, lat_bottom = tile_fraction_to_lonlat(float(x_end + 1), float(y_end + 1), zoom)
    extent = lonlat_to_web_mercator(np.array([lon_left, lon_right]), np.array([lat_bottom, lat_top]))
    x_min, x_max = float(np.min(extent[0])), float(np.max(extent[0]))
    y_min, y_max = float(np.min(extent[1])), float(np.max(extent[1]))
    return BasemapLayer(
        image=np.asarray(stitched.convert("RGBA")),
        extent=(x_min, x_max, y_min, y_max),
        attribution=attribution,
    )


def load_basemap_layer(bounds: tuple[float, float, float, float], render: TecMapRenderConfig) -> BasemapLayer | None:
    if not render.basemap_enabled:
        return None

    mode = str(render.basemap_mode or "off").strip().lower()
    if mode == "off":
        return None

    preferred_zoom = render.basemap_zoom or choose_tile_zoom(bounds, render.basemap_max_tiles)

    def _load_mode() -> BasemapLayer:
        last_error: Exception | None = None
        for zoom in range(preferred_zoom, 1, -1):
            try:
                if mode == "openstreetmap":
                    if render.basemap_cache_root is None:
                        raise RuntimeError("openstreetmap mode requires basemap_cache_root.")
                    return fetch_xyz_layer_at_zoom(
                        bounds,
                        tile_fetcher=lambda x, y, tile_zoom: fetch_openstreetmap_tile(
                            x,
                            y,
                            tile_zoom,
                            render.basemap_cache_root,
                            allow_network=True,
                        ),
                        zoom=zoom,
                        max_tiles=render.basemap_max_tiles,
                        attribution="© OpenStreetMap contributors",
                    )
                if mode == "cache_only":
                    if render.basemap_cache_root is None:
                        raise RuntimeError("cache_only mode requires basemap_cache_root.")
                    return fetch_xyz_layer_at_zoom(
                        bounds,
                        tile_fetcher=lambda x, y, tile_zoom: fetch_cached_xyz_tile(
                            x,
                            y,
                            tile_zoom,
                            render.basemap_cache_root / "openstreetmap",
                        ),
                        zoom=zoom,
                        max_tiles=render.basemap_max_tiles,
                        attribution="© OpenStreetMap contributors (cached)",
                    )
                if mode == "tile_server":
                    if not render.basemap_tile_server_url:
                        raise RuntimeError("tile_server mode requires basemap_tile_server_url.")
                    return fetch_xyz_layer_at_zoom(
                        bounds,
                        tile_fetcher=lambda x, y, tile_zoom: fetch_tile_server_tile(
                            x,
                            y,
                            tile_zoom,
                            render.basemap_tile_server_url,
                        ),
                        zoom=zoom,
                        max_tiles=render.basemap_max_tiles,
                        attribution=f"Tiles: {render.basemap_tile_server_url}",
                    )
                raise RuntimeError(f"Unsupported basemap mode: {render.basemap_mode}")
            except Exception as exc:
                last_error = exc
                continue

        source_name = {
            "openstreetmap": "OpenStreetMap tiles",
            "cache_only": "cached OpenStreetMap tiles",
            "tile_server": "HTTP XYZ tile server",
        }.get(mode, f"basemap mode {mode!r}")
        raise RuntimeError(f"{source_name} could not be loaded for zooms 2..{preferred_zoom}: {last_error}")

    try:
        return _load_mode()
    except Exception as exc:
        if render.basemap_fallback_to_plain:
            print(f"Warning: basemap mode '{mode}' unavailable; rendering without basemap: {exc}")
            return None
        raise


# ---------------------------------------------------------------------------
# Matplotlib frame rendering (ported from prototype; in-memory)
# ---------------------------------------------------------------------------

def choose_figure_size(width_units: float, height_units: float) -> tuple[float, float]:
    if width_units <= 0 or height_units <= 0:
        return 8.0, 6.0

    target_area = 54.0
    min_side = 4.8
    max_width = 14.0
    max_height = 10.0

    aspect = width_units / height_units
    width = math.sqrt(target_area * aspect)
    height = math.sqrt(target_area / aspect)

    scale_down = max(width / max_width, height / max_height, 1.0)
    width /= scale_down
    height /= scale_down

    scale_up = max(min_side / width if width < min_side else 1.0, min_side / height if height < min_side else 1.0)
    width *= scale_up
    height *= scale_up

    return width, height


def geographic_tick_values(min_value: float, max_value: float, count: int = 6) -> np.ndarray:
    if math.isclose(min_value, max_value):
        return np.array([min_value], dtype=float)
    return np.linspace(min_value, max_value, count)


def format_longitude(lon_deg: float) -> str:
    normalized = ((lon_deg + 180.0) % 360.0) - 180.0
    suffix = "E" if normalized >= 0 else "W"
    return f"{abs(normalized):.1f}{suffix}"


def format_latitude(lat_deg: float) -> str:
    suffix = "N" if lat_deg >= 0 else "S"
    return f"{abs(lat_deg):.1f}{suffix}"


def apply_geographic_ticks(
    ax: plt.Axes,
    *,
    lon_min: float,
    lon_max: float,
    lat_min: float,
    lat_max: float,
    projected: bool,
) -> None:
    lon_ticks = geographic_tick_values(lon_min, lon_max)
    lat_ticks = geographic_tick_values(lat_min, lat_max)

    if projected:
        mean_lat = (lat_min + lat_max) / 2.0
        mean_lon = (lon_min + lon_max) / 2.0
        x_ticks, _ = lonlat_to_web_mercator(lon_ticks, np.full(lon_ticks.shape, mean_lat))
        _, y_ticks = lonlat_to_web_mercator(np.full(lat_ticks.shape, mean_lon), lat_ticks)
    else:
        x_ticks = lon_ticks
        y_ticks = lat_ticks

    ax.set_xticks(x_ticks)
    ax.set_yticks(y_ticks)
    ax.set_xticklabels([format_longitude(float(value)) for value in lon_ticks])
    ax.set_yticklabels([format_latitude(float(value)) for value in lat_ticks])
    ax.set_xlabel("Longitude [deg]")
    ax.set_ylabel("Latitude [deg]")


def _interp_label(pipeline: TecMapConfig) -> str:
    method = str(pipeline.interpolation_method or "linear").strip().lower()
    if method == "lpi" and int(getattr(pipeline, "lpi_degree", 1)) >= 2:
        return "interp lpi(deg 2)"
    return f"interp {method}"


def pipeline_params_label(pipeline: TecMapConfig, render: TecMapRenderConfig) -> str:
    """One-line caption with the model constants the map was built from."""
    parts = [
        f"grid {pipeline.grid_resolution_deg:g}°",
        f"σg {pipeline.smoothing_sigma:g} cell",
        f"ΔT {int(pipeline.frame_minutes)} min",
        f"h_ion {pipeline.ionosphere_height_km:g} km",
        f"θ_min {pipeline.min_elevation_deg:g}°",
        (
            f"R_cov {pipeline.ipp_gradient_radius_km:g} km"
            if pipeline.ipp_gradient_radius_km is not None
            else "R_cov off"
        ),
        _interp_label(pipeline),
    ]
    if int(pipeline.vtec_smooth_epochs or 0) > 0:
        parts.append(f"t-median {int(pipeline.vtec_smooth_epochs)} ep")
    if pipeline.normalize_stations and pipeline.normalize_stations != "off":
        parts.append(f"normalize {pipeline.normalize_stations}")
    if int(render.upsample_factor or 1) > 1:
        parts.append(f"upsample {int(render.upsample_factor)}×")
    return "Model: " + " · ".join(parts)


def render_frame_png_bytes(
    *,
    frame_time: pd.Timestamp,
    frame: pd.DataFrame,
    station_positions: pd.DataFrame,
    grid_lon: np.ndarray,
    grid_lat: np.ndarray,
    grid: np.ndarray,
    bounds: tuple[float, float, float, float],
    color_limits: tuple[float, float],
    basemap_layer: BasemapLayer | None,
    pipeline: TecMapConfig,
    render: TecMapRenderConfig,
    field_spec: dict[str, Any] | None = None,
    image_format: str = "png",
    accuracy_label: str | None = None,
    params_label: str | None = None,
) -> bytes:
    spec = field_spec or _field_render_spec(render.field, render.signal_band)
    plot_cmap = spec["matplotlib_cmap"]
    colorbar_label = spec["colorbar_label"]
    title_prefix = spec["title_prefix"]
    lon_min, lon_max, lat_min, lat_max = bounds
    vmin, vmax = color_limits
    mean_lat = (lat_min + lat_max) / 2.0
    projected = basemap_layer is not None

    if projected:
        plot_grid_x, plot_grid_y = lonlat_to_web_mercator(grid_lon, grid_lat)
        station_x, station_y = lonlat_to_web_mercator(
            station_positions["site_lon"].to_numpy(),
            station_positions["site_lat"].to_numpy(),
        )
        sample_x, sample_y = lonlat_to_web_mercator(frame["ipp_lon"].to_numpy(), frame["ipp_lat"].to_numpy())
        x_min, y_min = lonlat_to_web_mercator(lon_min, lat_min)
        x_max, y_max = lonlat_to_web_mercator(lon_max, lat_max)
        width_units = abs(x_max - x_min)
        height_units = abs(y_max - y_min)
        x_limits = (min(x_min, x_max), max(x_min, x_max))
        y_limits = (min(y_min, y_max), max(y_min, y_max))
    else:
        plot_grid_x, plot_grid_y = grid_lon, grid_lat
        station_x = station_positions["site_lon"].to_numpy()
        station_y = station_positions["site_lat"].to_numpy()
        sample_x = frame["ipp_lon"].to_numpy()
        sample_y = frame["ipp_lat"].to_numpy()
        width_units = abs(lon_max - lon_min) * max(math.cos(math.radians(mean_lat)), 0.2)
        height_units = abs(lat_max - lat_min)
        x_limits = (lon_min, lon_max)
        y_limits = (lat_min, lat_max)

    fig, ax = plt.subplots(figsize=choose_figure_size(width_units, height_units), dpi=render.frame_dpi)
    if basemap_layer is not None:
        ax.imshow(
            basemap_layer.image,
            extent=basemap_layer.extent,
            origin="upper",
            alpha=render.basemap_alpha,
            zorder=0,
        )

    contour_levels = np.linspace(vmin, vmax, max(int(render.contour_levels), 3))
    overlay_alpha = render.vtec_layer_alpha if render.vtec_layer_alpha is not None else (0.72 if basemap_layer else 0.95)

    masked_grid = np.ma.masked_invalid(grid)
    mesh = None
    if np.ma.count(masked_grid) > 0:
        # extend="both": cells beyond the colour limits saturate at the end
        # colours instead of being left unfilled (visible as white holes on
        # fields with strong out-of-quantile areas, e.g. the IRI model).
        mesh = ax.contourf(
            plot_grid_x,
            plot_grid_y,
            masked_grid,
            levels=contour_levels,
            cmap=plot_cmap,
            alpha=overlay_alpha,
            antialiased=True,
            zorder=1,
            extend="both",
        )
        ax.contour(
            plot_grid_x,
            plot_grid_y,
            masked_grid,
            levels=contour_levels[::2],
            colors="white",
            linewidths=0.3,
            alpha=0.18,
            zorder=2,
        )

    ax.scatter(
        station_x,
        station_y,
        c="white",
        edgecolors="black",
        marker="^",
        s=60,
        label="Stations",
        zorder=3,
    )
    point_transform = spec.get("point_transform")
    if point_transform is None:
        # Fields without comparable per-point values (e.g. |∇VTEC|): render IPP
        # markers as position-only neutral dots.
        sample_plot = ax.scatter(
            sample_x,
            sample_y,
            c="white",
            edgecolors="black",
            s=20,
            label="IPP samples",
            zorder=4,
        )
    else:
        sample_plot = ax.scatter(
            sample_x,
            sample_y,
            c=point_transform(frame["vtec_tecu"].to_numpy()),
            cmap=plot_cmap,
            vmin=vmin,
            vmax=vmax,
            edgecolors="black",
            s=35,
            label="IPP samples",
            zorder=4,
        )

    if render.label_station_markers and "station" in station_positions.columns:
        for row in station_positions.itertuples(index=False):
            label_x, label_y = (
                lonlat_to_web_mercator(row.site_lon, row.site_lat) if projected else (row.site_lon, row.site_lat)
            )
            ax.text(
                label_x + width_units * 0.008,
                label_y + height_units * 0.012,
                str(row.station).upper(),
                fontsize=10,
                zorder=5,
                bbox={"facecolor": "white", "alpha": 0.55, "edgecolor": "none", "pad": 1.2},
            )

    if render.label_ipp_samples and "station" in frame.columns:
        for row in frame.itertuples(index=False):
            label_x, label_y = (
                lonlat_to_web_mercator(row.ipp_lon, row.ipp_lat) if projected else (row.ipp_lon, row.ipp_lat)
            )
            ax.text(
                label_x + width_units * 0.006,
                label_y - height_units * 0.008,
                f"IPP {str(row.station).upper()}",
                fontsize=9,
                zorder=5,
                bbox={"facecolor": "white", "alpha": 0.5, "edgecolor": "none", "pad": 1.0},
            )

    ax.set_title(f"{title_prefix} {frame_time:%Y-%m-%d %H:%M} UTC")
    apply_geographic_ticks(ax, lon_min=lon_min, lon_max=lon_max, lat_min=lat_min, lat_max=lat_max, projected=projected)

    ax.set_xlim(*x_limits)
    ax.set_ylim(*y_limits)
    if projected:
        ax.set_aspect("equal")
    else:
        ax.set_aspect(1.0 / max(math.cos(math.radians(mean_lat)), 0.2))
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper right")

    fig.colorbar(mesh if mesh is not None else sample_plot, ax=ax, label=colorbar_label)
    if accuracy_label:
        ax.text(
            0.01,
            0.985,
            accuracy_label,
            transform=ax.transAxes,
            fontsize=10,
            color="black",
            va="top",
            zorder=6,
            bbox={"facecolor": "white", "alpha": 0.65, "edgecolor": "none", "pad": 1.5},
        )
    if basemap_layer is not None:
        ax.text(
            0.01,
            0.01,
            basemap_layer.attribution,
            transform=ax.transAxes,
            fontsize=9,
            color="black",
            bbox={"facecolor": "white", "alpha": 0.55, "edgecolor": "none", "pad": 1.0},
        )

    if params_label:
        fig.text(
            0.01,
            0.006,
            params_label,
            fontsize=9,
            color="dimgray",
            va="bottom",
            ha="left",
        )

    buf = BytesIO()
    fig.savefig(buf, format=image_format, dpi=render.frame_dpi)
    plt.close(fig)
    return buf.getvalue()


def _grid_for_bounds(
    *,
    bounds: tuple[float, float, float, float],
    resolution_deg: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    lon_min, lon_max, lat_min, lat_max = bounds
    lon_axis = np.arange(lon_min, lon_max + resolution_deg, resolution_deg)
    lat_axis = np.arange(lat_min, lat_max + resolution_deg, resolution_deg)
    grid_lon, grid_lat = np.meshgrid(lon_axis, lat_axis)
    return lon_axis, lat_axis, grid_lon, grid_lat, lon_axis, lat_axis


def _bounds_for_frame_summary(frame_summary: pd.DataFrame) -> tuple[float, float, float, float]:
    lat_min = math.floor(float(frame_summary["ipp_lat"].min()) - 2)
    lat_max = math.ceil(float(frame_summary["ipp_lat"].max()) + 2)
    lon_min = math.floor(float(frame_summary["ipp_lon"].min()) - 2)
    lon_max = math.ceil(float(frame_summary["ipp_lon"].max()) + 2)
    return lon_min, lon_max, lat_min, lat_max


# ---------------------------------------------------------------------------
# Public renderers
# ---------------------------------------------------------------------------

def build_animation_gif_bytes(
    *,
    frame_summary: pd.DataFrame,
    pipeline: TecMapConfig,
    render: TecMapRenderConfig,
    max_frames: int = 800,
) -> bytes:
    if frame_summary.empty:
        raise RuntimeError("frame_summary is empty.")

    bounds = _bounds_for_frame_summary(frame_summary)
    lon_min, lon_max, lat_min, lat_max = bounds

    lon_axis = np.arange(lon_min, lon_max + pipeline.grid_resolution_deg, pipeline.grid_resolution_deg)
    lat_axis = np.arange(lat_min, lat_max + pipeline.grid_resolution_deg, pipeline.grid_resolution_deg)
    grid_lon, grid_lat = np.meshgrid(lon_axis, lat_axis)

    basemap_layer = load_basemap_layer(bounds, render)
    frame_groups = list(frame_summary.groupby("frame_time", sort=True))
    if len(frame_groups) > max_frames:
        raise RuntimeError(f"Refusing to render {len(frame_groups)} frames (max_frames={max_frames}).")

    station_positions = frame_summary.groupby("station", as_index=False).agg(site_lat=("site_lat", "first"), site_lon=("site_lon", "first"))

    field_spec = _field_render_spec(render.field, render.signal_band)
    grid_transform = field_spec.get("grid_transform")

    animation_format = (render.animation_format or "gif").strip().lower()
    if animation_format not in {"gif", "mp4", "webm"}:
        raise ValueError(f"Unsupported animation format: {render.animation_format!r}. Use gif, mp4 or webm.")

    # Pass 1: build the per-frame grid (and, for derived fields, transform it).
    model_mode = (render.model_mode or "off").strip().lower()
    computed_frames: list[tuple[pd.Timestamp, pd.DataFrame, np.ndarray]] = []
    plot_grid_lon, plot_grid_lat = upsample_coordinates(grid_lon, grid_lat, render.upsample_factor)

    model_grids: dict[pd.Timestamp, np.ndarray] = {}
    f107_meta: dict[str, dict[str, float | str]] = {}
    if model_mode != "off":
        model_grids, f107_meta = model_field_grids(
            [pd.Timestamp(ft) for ft, _ in frame_groups], plot_grid_lon, plot_grid_lat, field_spec, render
        )

    for frame_time, frame in frame_groups:
        ts = pd.Timestamp(frame_time)
        if model_mode == "iri":
            grid = model_grids[ts]
        else:
            grid, _, _ = compute_field_grid(
                frame, grid_lon, grid_lat, pipeline, grid_transform, upsample_factor=render.upsample_factor
            )
            if model_mode == "difference":
                grid = grid - model_grids[ts]
        computed_frames.append((ts, frame, grid))

    if model_mode != "off":
        field_spec = model_render_spec(field_spec, model_mode)

    # Determine global colour limits from the actual field that will be plotted.
    if model_mode == "difference":
        vmin, vmax = difference_color_limits([grid for _, _, grid in computed_frames])
    elif model_mode == "iri":
        vmin, vmax = model_color_limits([grid for _, _, grid in computed_frames])
    elif field_spec.get("derived_scale"):
        vmin, vmax = _derived_color_limits(field_spec, [grid for _, _, grid in computed_frames])
    else:
        vmin, vmax = _vtec_color_limits(frame_summary, pipeline)
    vmin, vmax = _apply_color_overrides(vmin, vmax, render)

    # Pass 2: render each frame against the shared colour scale.
    total_frames = len(computed_frames)
    params_label = pipeline_params_label(pipeline, render) if render.show_params else None
    if params_label and f107_meta:
        params_label = f"{params_label} | IRI F10.7: {_f107_caption(f107_meta)}"

    def _frame_annotation(frame: pd.DataFrame, grid: np.ndarray) -> str | None:
        parts: list[str] = []
        if render.show_accuracy:
            loso = frame_accuracy_label(frame, pipeline)
            if loso:
                parts.append(loso)
        if model_mode == "difference":
            diff = model_difference_label(grid, field_spec.get("hover_unit", ""))
            if diff:
                parts.append(diff)
        return "\n".join(parts) or None

    def rendered_png_frames():
        for idx, (frame_time, frame, grid) in enumerate(computed_frames):
            if idx % 25 == 0:
                logger.info("tec-map render: frame %d/%d (%s)", idx + 1, total_frames, frame_time)
            yield render_frame_png_bytes(
                frame_time=frame_time,
                frame=frame,
                station_positions=station_positions,
                grid_lon=plot_grid_lon,
                grid_lat=plot_grid_lat,
                grid=grid,
                bounds=bounds,
                color_limits=(float(vmin), float(vmax)),
                basemap_layer=basemap_layer,
                pipeline=pipeline,
                render=render,
                field_spec=field_spec,
                accuracy_label=_frame_annotation(frame, grid),
                params_label=params_label,
            )

    duration_seconds = max(float(render.gif_frame_duration_seconds), 0.02)
    if animation_format == "gif":
        return _encode_gif(
            rendered_png_frames(),
            duration_ms=int(round(duration_seconds * 1000.0)),
            high_quality=render.gif_high_quality,
        )
    return _encode_video(
        rendered_png_frames(),
        video_format=animation_format,
        frame_duration_seconds=duration_seconds,
    )


ANIMATION_MEDIA_TYPES = {
    "gif": "image/gif",
    "mp4": "video/mp4",
    "webm": "video/webm",
}


def _encode_gif(png_frames, *, duration_ms: int, high_quality: bool) -> bytes:
    """
    Stream PNG frames into an animated GIF.

    high_quality=True uses an adaptive MEDIANCUT palette with Floyd–Steinberg
    dithering (smoother colour gradients, slower); otherwise FASTOCTREE without
    dithering (the original fast path).
    """
    out = BytesIO()
    wrote_any_frame = False

    for idx, png_bytes in enumerate(png_frames):
        with Image.open(BytesIO(png_bytes)) as frame_image:
            if high_quality:
                # MEDIANCUT requires RGB input; dithering hides palette banding.
                paletted = frame_image.convert("RGB").quantize(
                    colors=256,
                    method=Image.Quantize.MEDIANCUT,
                    dither=Image.Dither.FLOYDSTEINBERG,
                )
            else:
                paletted = frame_image.quantize(
                    colors=256,
                    method=Image.Quantize.FASTOCTREE,
                    dither=Image.Dither.NONE,
                )
            try:
                paletted.info["background"] = 0
                paletted.info.pop("transparency", None)
                if idx == 0:
                    header_chunks, _ = GifImagePlugin.getheader(
                        paletted,
                        info={"loop": 0, "background": 0},
                    )
                    for chunk in header_chunks:
                        out.write(chunk)
                for chunk in GifImagePlugin.getdata(
                    paletted,
                    duration=max(duration_ms, 20),
                    disposal=2,
                    include_color_table=(idx > 0),
                ):
                    out.write(chunk)
                wrote_any_frame = True
            finally:
                paletted.close()

    if not wrote_any_frame:
        raise RuntimeError("No frames were rendered.")

    out.write(b";")
    return out.getvalue()


def _encode_video(png_frames, *, video_format: str, frame_duration_seconds: float) -> bytes:
    """
    Stream PNG frames into an MP4 (H.264) or WebM (VP9) container via
    imageio/imageio-ffmpeg. Full 24-bit colour — no GIF palette banding —
    and typically several times smaller than the equivalent GIF.
    """
    try:
        import imageio.v2 as imageio
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError(
            "MP4/WebM export requires the 'imageio' and 'imageio-ffmpeg' packages."
        ) from exc

    fps = min(max(1.0 / max(frame_duration_seconds, 1e-3), 0.5), 60.0)
    if video_format == "mp4":
        codec = "libx264"
        output_params = ["-crf", "18", "-preset", "medium"]
    else:
        codec = "libvpx-vp9"
        output_params = ["-crf", "30", "-b:v", "0"]

    # The ffmpeg writer needs a seekable file, not a pipe.
    tmp = tempfile.NamedTemporaryFile(suffix=f".{video_format}", delete=False)
    tmp_path = tmp.name
    tmp.close()
    wrote_any_frame = False
    try:
        writer = imageio.get_writer(
            tmp_path,
            fps=fps,
            codec=codec,
            quality=None,
            pixelformat="yuv420p",
            macro_block_size=1,
            output_params=output_params,
        )
        try:
            for png_bytes in png_frames:
                with Image.open(BytesIO(png_bytes)) as frame_image:
                    frame_array = np.asarray(frame_image.convert("RGB"))
                # yuv420p requires even dimensions: crop at most one row/column.
                frame_array = frame_array[
                    : frame_array.shape[0] // 2 * 2,
                    : frame_array.shape[1] // 2 * 2,
                ]
                writer.append_data(frame_array)
                wrote_any_frame = True
        finally:
            writer.close()

        if not wrote_any_frame:
            raise RuntimeError("No frames were rendered.")
        return Path(tmp_path).read_bytes()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def build_frame_image_bytes(
    *,
    frame_summary: pd.DataFrame,
    frame_time: pd.Timestamp,
    pipeline: TecMapConfig,
    render: TecMapRenderConfig,
    image_format: str = "png",
) -> bytes:
    """
    Publication-quality single-frame image (PNG or SVG) at render.frame_dpi.

    Same visual pipeline as one animation frame: field transforms, coverage
    mask, optional upsampling and basemap. Colour limits come from this frame
    (or from render.color_min/color_max when set, for cross-frame consistency).
    """
    fmt = (image_format or "png").strip().lower()
    if fmt not in {"png", "svg"}:
        raise ValueError(f"Unsupported image format: {image_format!r}. Use png or svg.")

    frame = frame_summary[frame_summary["frame_time"] == frame_time].copy()
    if frame.empty:
        raise RuntimeError(f"No samples available for requested frame_time={frame_time}.")

    bounds = _bounds_for_frame_summary(frame)
    lon_min, lon_max, lat_min, lat_max = bounds
    lon_axis = np.arange(lon_min, lon_max + pipeline.grid_resolution_deg, pipeline.grid_resolution_deg)
    lat_axis = np.arange(lat_min, lat_max + pipeline.grid_resolution_deg, pipeline.grid_resolution_deg)
    grid_lon, grid_lat = np.meshgrid(lon_axis, lat_axis)

    field_spec = _field_render_spec(render.field, render.signal_band)
    model_mode = (render.model_mode or "off").strip().lower()

    model_grid: np.ndarray | None = None
    f107_meta: dict[str, dict[str, float | str]] = {}
    if model_mode != "off":
        plot_grid_lon, plot_grid_lat = upsample_coordinates(grid_lon, grid_lat, render.upsample_factor)
        model_grids, f107_meta = model_field_grids(
            [pd.Timestamp(frame_time)], plot_grid_lon, plot_grid_lat, field_spec, render
        )
        model_grid = model_grids[pd.Timestamp(frame_time)]

    if model_mode == "iri":
        grid = model_grid
        plot_grid_lon, plot_grid_lat = upsample_coordinates(grid_lon, grid_lat, render.upsample_factor)
    else:
        grid, plot_grid_lon, plot_grid_lat = compute_field_grid(
            frame, grid_lon, grid_lat, pipeline, field_spec.get("grid_transform"), upsample_factor=render.upsample_factor
        )
        if model_mode == "difference":
            grid = grid - model_grid

    if model_mode != "off":
        field_spec = model_render_spec(field_spec, model_mode)

    if model_mode == "difference":
        vmin, vmax = difference_color_limits([grid])
    elif model_mode == "iri":
        vmin, vmax = model_color_limits([grid])
    elif field_spec.get("derived_scale"):
        vmin, vmax = _derived_color_limits(field_spec, [grid])
    else:
        vmin, vmax = _vtec_color_limits(frame_summary, pipeline)
    vmin, vmax = _apply_color_overrides(vmin, vmax, render)

    station_positions = frame_summary.groupby("station", as_index=False).agg(
        site_lat=("site_lat", "first"),
        site_lon=("site_lon", "first"),
    )
    basemap_layer = load_basemap_layer(bounds, render)

    annotation_parts: list[str] = []
    if render.show_accuracy:
        loso = frame_accuracy_label(frame, pipeline)
        if loso:
            annotation_parts.append(loso)
    if model_mode == "difference":
        diff_label = model_difference_label(grid, field_spec.get("hover_unit", ""))
        if diff_label:
            annotation_parts.append(diff_label)

    params_label = pipeline_params_label(pipeline, render) if render.show_params else None
    if params_label and f107_meta:
        params_label = f"{params_label} | IRI F10.7: {_f107_caption(f107_meta)}"

    return render_frame_png_bytes(
        frame_time=pd.Timestamp(frame_time),
        frame=frame,
        station_positions=station_positions,
        grid_lon=plot_grid_lon,
        grid_lat=plot_grid_lat,
        grid=grid,
        bounds=bounds,
        color_limits=(float(vmin), float(vmax)),
        basemap_layer=basemap_layer,
        pipeline=pipeline,
        render=render,
        field_spec=field_spec,
        image_format=fmt,
        accuracy_label="\n".join(annotation_parts) or None,
        params_label=params_label,
    )


def build_snapshot_plotly_json(
    *,
    frame_summary: pd.DataFrame,
    frame_time: pd.Timestamp,
    pipeline: TecMapConfig,
    render: TecMapRenderConfig,
    include_grid: bool = True,
) -> dict[str, Any]:
    frame = frame_summary[frame_summary["frame_time"] == frame_time].copy()
    if frame.empty:
        raise RuntimeError(f"No samples available for requested frame_time={frame_time}.")

    bounds = _bounds_for_frame_summary(frame)
    lon_min, lon_max, lat_min, lat_max = bounds

    station_positions = frame_summary.groupby("station", as_index=False).agg(
        site_lat=("site_lat", "first"),
        site_lon=("site_lon", "first"),
    )

    field_spec = _field_render_spec(render.field, render.signal_band)
    grid_transform = field_spec.get("grid_transform")
    is_derived_field = bool(field_spec.get("derived_scale"))
    field_short = {
        "vtec_gradient": "|∇VTEC|",
        "gdd": "|D|",
        "b_k": "B_k",
    }.get((render.field or "").strip().lower(), "VTEC")

    fig = go.Figure()

    model_mode = (render.model_mode or "off").strip().lower()
    f107_meta: dict[str, dict[str, float | str]] = {}
    grid_for_scale: np.ndarray | None = None
    if include_grid:
        lon_axis = np.arange(lon_min, lon_max + pipeline.grid_resolution_deg, pipeline.grid_resolution_deg)
        lat_axis = np.arange(lat_min, lat_max + pipeline.grid_resolution_deg, pipeline.grid_resolution_deg)
        grid_lon, grid_lat = np.meshgrid(lon_axis, lat_axis)
        if model_mode != "off":
            model_grids, f107_meta = model_field_grids(
                [pd.Timestamp(frame_time)], grid_lon, grid_lat, field_spec, render
            )
            model_grid = model_grids[pd.Timestamp(frame_time)]
            if model_mode == "iri":
                grid_for_scale = model_grid
            else:
                grid_for_scale, _, _ = compute_field_grid(frame, grid_lon, grid_lat, pipeline, grid_transform)
                grid_for_scale = grid_for_scale - model_grid
        else:
            grid_for_scale, _, _ = compute_field_grid(frame, grid_lon, grid_lat, pipeline, grid_transform)

    if model_mode != "off":
        field_spec = model_render_spec(field_spec, model_mode)

    # Shared colour scale. For VTEC: 5–95% quantile over the full selection.
    # For derived fields: vmin_floor (or 5% quantile) → 95% quantile of the
    # snapshot's transformed values. Differences: symmetric around zero.
    if model_mode == "difference":
        vmin, vmax = difference_color_limits([grid_for_scale] if grid_for_scale is not None else [])
    elif model_mode == "iri":
        vmin, vmax = model_color_limits([grid_for_scale] if grid_for_scale is not None else [])
    elif is_derived_field:
        grids = [grid_for_scale] if grid_for_scale is not None else []
        vmin, vmax = _derived_color_limits(field_spec, grids)
    else:
        vmin, vmax = _vtec_color_limits(frame_summary, pipeline)
    vmin, vmax = _apply_color_overrides(vmin, vmax, render)

    if include_grid and grid_for_scale is not None:
        # Prefer strict-JSON safe z: replace NaN with None.
        z_rows: list[list[float | None]] = []
        for row in grid_for_scale:
            z_rows.append([float(v) if np.isfinite(v) else None for v in row])

        heatmap_name = f"{field_short} (interpolated)" if is_derived_field else "Interpolated VTEC"
        hover_unit = field_spec["hover_unit"]
        z_label = field_short
        fig.add_trace(
            go.Heatmap(
                x=lon_axis,
                y=lat_axis,
                z=z_rows,
                colorscale=field_spec["plotly_colorscale"],
                zmin=float(vmin),
                zmax=float(vmax),
                colorbar={"title": field_spec["colorbar_label"]},
                hovertemplate="Lon %{x:.2f}<br>Lat %{y:.2f}<br>" + z_label + " %{z:.2f} " + hover_unit + "<extra></extra>",
                name=heatmap_name,
                showscale=True,
                opacity=render.vtec_layer_alpha if render.vtec_layer_alpha is not None else 0.95,
                zsmooth="best",
            )
        )

    station_codes = [str(v).upper() for v in station_positions["station"].tolist()]
    fig.add_trace(
        go.Scatter(
            x=station_positions["site_lon"],
            y=station_positions["site_lat"],
            mode="markers+text" if render.label_station_markers else "markers",
            text=station_codes if render.label_station_markers else None,
            textposition="top right",
            marker={
                "symbol": "triangle-up",
                "size": 12,
                "color": "white",
                "line": {"color": "black", "width": 1},
            },
            name="Stations",
            customdata=station_codes,
            hovertemplate="Station %{customdata}<br>Lon %{x:.3f}<br>Lat %{y:.3f}<extra></extra>",
        )
    )

    ipp_station_codes = [str(v).upper() for v in frame["station"].tolist()]
    point_transform = field_spec.get("point_transform")
    if point_transform is None:
        # Per-IPP values are not on this field's colour scale; show positions only.
        ipp_marker: dict[str, Any] = {
            "size": 8,
            "color": "white",
            "line": {"color": "black", "width": 0.7},
        }
        ipp_hover = "Station %{customdata}<br>Lon %{x:.3f}<br>Lat %{y:.3f}<br>(per-IPP values not on this scale)<extra></extra>"
    else:
        point_values = point_transform(frame["vtec_tecu"].to_numpy())
        point_values = [float(v) if np.isfinite(v) else None for v in np.asarray(point_values, dtype=float)]
        ipp_marker = {
            "size": 10,
            "color": point_values,
            "colorscale": field_spec["plotly_colorscale"],
            "cmin": float(vmin),
            "cmax": float(vmax),
            "line": {"color": "black", "width": 0.7},
        }
        if not include_grid:
            ipp_marker["showscale"] = True
            ipp_marker["colorbar"] = {"title": field_spec["colorbar_label"]}
        else:
            ipp_marker["showscale"] = False
        ipp_hover = (
            "Station %{customdata}<br>Lon %{x:.3f}<br>Lat %{y:.3f}<br>"
            + field_short + " %{marker.color:.2f} " + field_spec["hover_unit"] + "<extra></extra>"
        )

    fig.add_trace(
        go.Scatter(
            x=frame["ipp_lon"],
            y=frame["ipp_lat"],
            mode="markers",
            marker=ipp_marker,
            name="IPP samples",
            hovertemplate=ipp_hover,
            customdata=ipp_station_codes,
            showlegend=True,
        )
    )

    fig.update_layout(
        title=f"{field_spec['title_prefix']} {frame_time:%Y-%m-%d %H:%M} UTC",
        xaxis={"title": "Longitude [deg]", "range": [lon_min, lon_max]},
        yaxis={"title": "Latitude [deg]", "range": [lat_min, lat_max], "scaleanchor": "x", "scaleratio": 1.0},
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "right", "x": 1},
        margin={"l": 50, "r": 20, "t": 60, "b": 45},
        template="plotly_white",
        font={"family": PLOTLY_MAP_FONT_FAMILY, "size": 14},
    )

    corner_labels: list[str] = []
    if render.show_accuracy:
        accuracy_label = frame_accuracy_label(frame, pipeline)
        if accuracy_label:
            corner_labels.append(accuracy_label)
    if model_mode == "difference" and grid_for_scale is not None:
        diff_label = model_difference_label(grid_for_scale, field_spec.get("hover_unit", ""))
        if diff_label:
            corner_labels.append(diff_label)
    if corner_labels:
        fig.add_annotation(
            text="<br>".join(corner_labels),
            xref="paper",
            yref="paper",
            x=0.01,
            y=0.99,
            xanchor="left",
            yanchor="top",
            showarrow=False,
            font={"family": PLOTLY_MAP_FONT_FAMILY, "size": 13, "color": "black"},
            bgcolor="rgba(255,255,255,0.65)",
        )

    if render.show_params:
        params_text = pipeline_params_label(pipeline, render)
        if f107_meta:
            params_text = f"{params_text} | IRI F10.7: {_f107_caption(f107_meta)}"
        fig.update_layout(margin={"l": 50, "r": 20, "t": 60, "b": 80})
        fig.add_annotation(
            text=params_text,
            xref="paper",
            yref="paper",
            x=0.0,
            y=-0.16,
            xanchor="left",
            yanchor="top",
            showarrow=False,
            font={"family": PLOTLY_MAP_FONT_FAMILY, "size": 12, "color": "gray"},
        )

    # Requirement: based on Figure.to_plotly_json(); ensure strict JSON types.
    raw = fig.to_plotly_json()
    return json.loads(json.dumps(raw, cls=PlotlyJSONEncoder))
