"""Render dTEC pierce points on interactive and animated maps."""

from __future__ import annotations

import json
import math
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from matplotlib.figure import Figure

from app.tec_map_parallel import Progress
from app.tec_map_render import (
    PLOTLY_MAP_FONT_FAMILY,
    BasemapLayer,
    TecMapRenderConfig,
    apply_geographic_ticks,
    choose_figure_size,
    encode_animation,
    load_basemap_layer,
    lonlat_to_web_mercator,
)
from tec_variations.science import terminator
from tec_variations.settings import PeriodBand


@dataclass(frozen=True)
class DtecFigure:
    """Map bounds, data metadata and color scale shared by every frame."""

    band: PeriodBand
    shell_height_km: float
    bounds: tuple[float, float, float, float]
    limits: tuple[float, float]
    show_terminator: bool

    @property
    def band_label(self) -> str:
        short = str(self.band.shortest_minutes).removesuffix(".0")
        long = str(self.band.longest_minutes).removesuffix(".0")
        return f"dTEC {short}–{long} min"

    @property
    def shell_label(self) -> str:
        return f"Shell height {self.shell_height_km:g} km"

    def title(self, instant: datetime, count: int) -> str:
        points = "point" if count == 1 else "points"
        return f"{instant:%Y-%m-%d %H:%M:%S} UTC · {self.band_label} · {count} {points}"

    def terminator_line(self, instant: datetime) -> tuple[np.ndarray, np.ndarray]:
        seconds = (instant - instant.replace(hour=0, minute=0, second=0, microsecond=0)).total_seconds()
        return terminator(instant.date(), seconds, self.shell_height_km)


def snapshot_json(figure: DtecFigure, instant: datetime, frame: pd.DataFrame) -> dict[str, Any]:
    """Return the interactive Plotly figure in the IonMaps snapshot format."""
    lon_min, lon_max, lat_min, lat_max = figure.bounds
    low, high = figure.limits
    plot = go.Figure(
        go.Scatter(
            x=frame["ipp_lon"].tolist(),
            y=frame["ipp_lat"].tolist(),
            mode="markers",
            name="IPP samples",
            customdata=frame[["station", "satellite", "elevation_deg"]].values.tolist(),
            marker={
                "color": frame["dtec"].tolist(),
                "cmin": low,
                "cmax": high,
                "colorscale": "RdBu",
                "reversescale": True,
                "colorbar": {"title": f"{figure.band_label} (TECU)"},
                "showscale": True,
                "size": 8,
                "line": {"width": 0.5, "color": "black"},
            },
            hovertemplate=(
                "Station %{customdata[0]}<br>Satellite %{customdata[1]}<br>"
                "Elevation %{customdata[2]:.1f}°<br>dTEC %{marker.color:.3f} TECU<extra></extra>"
            ),
        )
    )
    if figure.show_terminator:
        x, y = figure.terminator_line(instant)
        plot.add_scatter(
            x=x.tolist(),
            y=y.tolist(),
            mode="lines",
            name="Solar terminator",
            line={"color": "dimgray", "dash": "dash"},
            hoverinfo="skip",
        )
    plot.update_layout(
        title=f"{figure.title(instant, len(frame))}<br><sup>{figure.shell_label}</sup>",
        xaxis={"title": "Longitude [deg]", "range": [lon_min, lon_max]},
        yaxis={"title": "Latitude [deg]", "range": [lat_min, lat_max], "scaleanchor": "x"},
        template="plotly_white",
        font={"family": PLOTLY_MAP_FONT_FAMILY, "size": 14},
        legend={"orientation": "h", "x": 1, "xanchor": "right", "y": 1.02, "yanchor": "bottom"},
        margin={"l": 50, "r": 20, "t": 90, "b": 45},
    )
    return json.loads(plot.to_json())


def render_frame_png(
    figure: DtecFigure,
    instant: datetime,
    frame: pd.DataFrame,
    render: TecMapRenderConfig,
    basemap: BasemapLayer | None = None,
) -> bytes:
    """Draw one IPP frame, including a colorbar for a frame with no samples."""
    lon_min, lon_max, lat_min, lat_max = figure.bounds
    projected = basemap is not None
    mean_lat = (lat_min + lat_max) / 2
    lon_scale = max(math.cos(math.radians(mean_lat)), 0.2)
    x, y = frame["ipp_lon"].to_numpy(), frame["ipp_lat"].to_numpy()
    x_min, x_max, y_min, y_max = figure.bounds
    if projected:
        x, y = lonlat_to_web_mercator(x, y)
        x_min, y_min = map(float, lonlat_to_web_mercator(lon_min, lat_min))
        x_max, y_max = map(float, lonlat_to_web_mercator(lon_max, lat_max))
    width = (x_max - x_min) * (1 if projected else lon_scale)
    fig = Figure(figsize=choose_figure_size(width, y_max - y_min), dpi=render.frame_dpi)
    ax = fig.add_subplot(111)
    if basemap is not None:
        ax.imshow(basemap.image, extent=basemap.extent, origin="upper", alpha=render.basemap_alpha, zorder=0)
        ax.text(
            0.01,
            0.01,
            basemap.attribution,
            transform=ax.transAxes,
            fontsize=9,
            bbox={"facecolor": "white", "alpha": 0.55, "edgecolor": "none", "pad": 1.0},
        )
    if figure.show_terminator:
        line_x, line_y = figure.terminator_line(instant)
        line = lonlat_to_web_mercator(line_x, line_y) if projected else (line_x, line_y)
        ax.plot(*line, "--", color="dimgray", linewidth=1, label="Solar terminator")
        ax.legend(loc="upper right")
    low, high = figure.limits
    samples = ax.scatter(
        x,
        y,
        c=frame["dtec"].to_numpy(),
        cmap="RdBu_r",
        vmin=low,
        vmax=high,
        s=20,
        edgecolors="black",
        linewidths=0.25,
        zorder=3,
    )
    fig.colorbar(samples, ax=ax, label=f"{figure.band_label} (TECU)")
    fig.text(0.01, 0.006, figure.shell_label, fontsize=9, color="dimgray", va="bottom")
    ax.set_title(figure.title(instant, len(frame)))
    apply_geographic_ticks(ax, lon_min=lon_min, lon_max=lon_max, lat_min=lat_min, lat_max=lat_max, projected=projected)
    ax.set(xlim=(x_min, x_max), ylim=(y_min, y_max), aspect=1 if projected else 1 / lon_scale)
    ax.grid(True, alpha=0.3)
    output = BytesIO()
    fig.savefig(output, format="png", dpi=render.frame_dpi)
    fig.clear()
    return output.getvalue()


def render_animation(
    figure: DtecFigure,
    frames: dict[datetime, pd.DataFrame],
    render: TecMapRenderConfig,
    progress: Progress,
) -> bytes:
    """Stream uniformly timed dTEC frames to the shared animation encoders."""
    basemap = load_basemap_layer(figure.bounds, render)
    count = len(frames)

    def png_frames() -> Iterator[bytes]:
        for index, (instant, frame) in enumerate(frames.items(), start=1):
            png = render_frame_png(figure, instant, frame, render, basemap)
            progress("drawing", index, count)
            yield png
        progress("encoding", count, count)

    return encode_animation(png_frames(), render, render.animation_format)
