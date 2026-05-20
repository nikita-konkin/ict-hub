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
import math
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
from PIL import GifImagePlugin, Image
from scipy.interpolate import griddata
from scipy.ndimage import gaussian_filter

import matplotlib

matplotlib.use("Agg")  # headless/server rendering
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap

import plotly.graph_objects as go
from _plotly_utils.utils import PlotlyJSONEncoder

from app.tec_map_pipeline import TecMapConfig


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


@dataclass(frozen=True, slots=True)
class TecMapRenderConfig:
    contour_levels: int = 128
    label_station_markers: bool = True
    label_ipp_samples: bool = False

    basemap_enabled: bool = False
    basemap_mode: str = "off"
    basemap_zoom: int | None = None
    basemap_max_tiles: int = 16
    basemap_alpha: float = 0.28
    vtec_layer_alpha: float | None = None

    frame_dpi: int = 120
    gif_frame_duration_seconds: float = 0.8

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


def soften_coverage_mask(mask: np.ndarray, pipeline: TecMapConfig) -> np.ndarray:
    mask_bool = np.asarray(mask, dtype=bool)
    sigma_cells = float(getattr(pipeline, "coverage_mask_smoothing_cells", 0.0) or 0.0)
    if sigma_cells <= 0 or mask_bool.size == 0:
        return mask_bool

    if mask_bool.all() or not mask_bool.any():
        return mask_bool

    softened = gaussian_filter(mask_bool.astype(float), sigma=sigma_cells, mode="nearest")
    return softened >= 0.5


def interpolate_frame(
    frame: pd.DataFrame,
    grid_lon: np.ndarray,
    grid_lat: np.ndarray,
    pipeline: TecMapConfig,
    coverage_mask: np.ndarray | None = None,
) -> np.ndarray:
    points = frame[["ipp_lon", "ipp_lat"]].to_numpy()
    values = frame["vtec_tecu"].to_numpy()

    if len(frame) >= 3:
        primary = griddata(points, values, (grid_lon, grid_lat), method=pipeline.interpolation_method)
        fallback = griddata(points, values, (grid_lon, grid_lat), method=pipeline.fallback_interpolation_method)
        interpolated = np.where(np.isnan(primary), fallback, primary)
    else:
        interpolated = griddata(points, values, (grid_lon, grid_lat), method=pipeline.fallback_interpolation_method)

    if coverage_mask is None:
        coverage_mask = ipp_coverage_mask(frame, grid_lon, grid_lat, pipeline)
    return np.where(coverage_mask, interpolated, np.nan)


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
) -> bytes:
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
        mesh = ax.contourf(
            plot_grid_x,
            plot_grid_y,
            masked_grid,
            levels=contour_levels,
            cmap=TEC_MAP_MATPLOTLIB_CMAP,
            alpha=overlay_alpha,
            antialiased=True,
            zorder=1,
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
    sample_plot = ax.scatter(
        sample_x,
        sample_y,
        c=frame["vtec_tecu"],
        cmap=TEC_MAP_MATPLOTLIB_CMAP,
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
                fontsize=8,
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
                fontsize=7,
                zorder=5,
                bbox={"facecolor": "white", "alpha": 0.5, "edgecolor": "none", "pad": 1.0},
            )

    ax.set_title(f"VTEC map {frame_time:%Y-%m-%d %H:%M} UTC")
    apply_geographic_ticks(ax, lon_min=lon_min, lon_max=lon_max, lat_min=lat_min, lat_max=lat_max, projected=projected)

    ax.set_xlim(*x_limits)
    ax.set_ylim(*y_limits)
    if projected:
        ax.set_aspect("equal")
    else:
        ax.set_aspect(1.0 / max(math.cos(math.radians(mean_lat)), 0.2))
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper right")

    fig.colorbar(mesh if mesh is not None else sample_plot, ax=ax, label="VTEC [TECU]")
    if basemap_layer is not None:
        ax.text(
            0.01,
            0.01,
            basemap_layer.attribution,
            transform=ax.transAxes,
            fontsize=7,
            color="black",
            bbox={"facecolor": "white", "alpha": 0.55, "edgecolor": "none", "pad": 1.0},
        )

    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=render.frame_dpi)
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

    vmin, vmax = np.quantile(frame_summary["vtec_tecu"], [0.05, 0.95])
    if math.isclose(float(vmin), float(vmax)):
        vmin -= 1.0
        vmax += 1.0
    if pipeline.enforce_nonnegative_vtec:
        vmin = max(float(vmin), 0.0)

    duration_ms = max(int(round(render.gif_frame_duration_seconds * 1000.0)), 20)
    out = BytesIO()
    wrote_any_frame = False

    for idx, (frame_time, frame) in enumerate(frame_groups):
        coverage_mask = ipp_coverage_mask(frame, grid_lon, grid_lat, pipeline)
        coverage_mask = soften_coverage_mask(coverage_mask, pipeline)
        grid = interpolate_frame(frame, grid_lon, grid_lat, pipeline, coverage_mask=coverage_mask)
        grid = smooth_grid(grid, pipeline.smoothing_sigma)
        grid = np.where(coverage_mask, grid, np.nan)
        if pipeline.enforce_nonnegative_vtec:
            grid = np.where(np.isfinite(grid), np.maximum(grid, 0.0), grid)

        png_bytes = render_frame_png_bytes(
            frame_time=pd.Timestamp(frame_time),
            frame=frame,
            station_positions=station_positions,
            grid_lon=grid_lon,
            grid_lat=grid_lat,
            grid=grid,
            bounds=bounds,
            color_limits=(float(vmin), float(vmax)),
            basemap_layer=basemap_layer,
            pipeline=pipeline,
            render=render,
        )

        with Image.open(BytesIO(png_bytes)) as frame_image:
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
                    duration=duration_ms,
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

    # Shared color scale logic (prototype: 5–95% quantile over full selection)
    vmin, vmax = np.quantile(frame_summary["vtec_tecu"], [0.05, 0.95])
    if math.isclose(float(vmin), float(vmax)):
        vmin -= 1.0
        vmax += 1.0
    if pipeline.enforce_nonnegative_vtec:
        vmin = max(float(vmin), 0.0)

    fig = go.Figure()

    if include_grid:
        lon_axis = np.arange(lon_min, lon_max + pipeline.grid_resolution_deg, pipeline.grid_resolution_deg)
        lat_axis = np.arange(lat_min, lat_max + pipeline.grid_resolution_deg, pipeline.grid_resolution_deg)
        grid_lon, grid_lat = np.meshgrid(lon_axis, lat_axis)

        coverage_mask = ipp_coverage_mask(frame, grid_lon, grid_lat, pipeline)
        coverage_mask = soften_coverage_mask(coverage_mask, pipeline)
        grid = interpolate_frame(frame, grid_lon, grid_lat, pipeline, coverage_mask=coverage_mask)
        grid = smooth_grid(grid, pipeline.smoothing_sigma)
        grid = np.where(coverage_mask, grid, np.nan)
        if pipeline.enforce_nonnegative_vtec:
            grid = np.where(np.isfinite(grid), np.maximum(grid, 0.0), grid)

        # Prefer strict-JSON safe z: replace NaN with None.
        z_rows: list[list[float | None]] = []
        for row in grid:
            z_rows.append([float(v) if np.isfinite(v) else None for v in row])

        fig.add_trace(
            go.Heatmap(
                x=lon_axis,
                y=lat_axis,
                z=z_rows,
                colorscale=TEC_MAP_PLOTLY_COLORSCALE,
                zmin=float(vmin),
                zmax=float(vmax),
                colorbar={"title": "VTEC [TECU]"},
                hovertemplate="Lon %{x:.2f}<br>Lat %{y:.2f}<br>VTEC %{z:.2f} TECU<extra></extra>",
                name="Interpolated VTEC",
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
    ipp_marker: dict[str, Any] = {
        "size": 10,
        "color": frame["vtec_tecu"],
        "colorscale": TEC_MAP_PLOTLY_COLORSCALE,
        "cmin": float(vmin),
        "cmax": float(vmax),
        "line": {"color": "black", "width": 0.7},
    }
    # Avoid duplicate colorbars when the contour layer already shows one.
    if not include_grid:
        ipp_marker["showscale"] = True
        ipp_marker["colorbar"] = {"title": "VTEC [TECU]"}
    else:
        ipp_marker["showscale"] = False

    fig.add_trace(
        go.Scatter(
            x=frame["ipp_lon"],
            y=frame["ipp_lat"],
            mode="markers",
            marker=ipp_marker,
            name="IPP samples",
            hovertemplate="Station %{customdata}<br>Lon %{x:.3f}<br>Lat %{y:.3f}<br>VTEC %{marker.color:.2f} TECU<extra></extra>",
            customdata=ipp_station_codes,
            showlegend=True,
        )
    )

    fig.update_layout(
        title=f"VTEC map {frame_time:%Y-%m-%d %H:%M} UTC",
        xaxis={"title": "Longitude [deg]", "range": [lon_min, lon_max]},
        yaxis={"title": "Latitude [deg]", "range": [lat_min, lat_max], "scaleanchor": "x", "scaleratio": 1.0},
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "right", "x": 1},
        margin={"l": 50, "r": 20, "t": 60, "b": 45},
        template="plotly_white",
    )

    # Requirement: based on Figure.to_plotly_json(); ensure strict JSON types.
    raw = fig.to_plotly_json()
    return json.loads(json.dumps(raw, cls=PlotlyJSONEncoder))
