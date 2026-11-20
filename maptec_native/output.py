"""Legacy-column DAT output and offline matplotlib maps."""

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.figure import Figure
from numpy.typing import NDArray

from .science import terminator
from .settings import MapSettings


@dataclass(frozen=True)
class MapPoint:
    """One filtered sample and its source station and satellite file."""

    latitude: float
    longitude: float
    di: float
    smoothed_tec: float
    azimuth: float
    elevation: float
    station_latitude: float
    station_longitude: float
    site: str
    filename: str

    def format_row(self, ut: float) -> str:
        return (
            f"{ut:10.4f}{self.latitude:10.2f}{self.longitude:10.2f}{self.di:14.3f}{self.smoothed_tec:14.3f}"
            f"{self.azimuth:8.2f}{self.elevation:8.2f}{self.station_latitude:8.2f}{self.station_longitude:8.2f}"
            f" {self.site} {self.filename}\n"
        )


def read_contour(path: Path) -> NDArray[np.float64]:
    """Read the two-column world contour, preserving blank-line breaks."""
    rows = []
    for raw in path.read_text(encoding="latin-1").splitlines():
        line = raw.strip()
        if line.startswith("#"):
            continue
        if not line:
            rows.append((np.nan, np.nan))
            continue
        try:
            longitude, latitude = map(float, line.split()[:2])
        except ValueError as exc:
            raise ValueError(f"Invalid world contour row in {path.name}") from exc
        if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
            raise ValueError(f"World contour coordinates are out of range in {path.name}")
        rows.append((longitude, latitude))
    return np.asarray(rows, dtype=float).reshape(-1, 2)


class MapRenderer:
    """Reuse the axes and static map elements across a day's frames."""

    def __init__(self, settings: MapSettings, contour: NDArray[np.float64] | None, solar: bool):
        self.settings = settings
        self.solar = solar
        self.figure = Figure(figsize=(8, 6), dpi=100, layout="constrained")
        FigureCanvasAgg(self.figure)
        self.axes = self.figure.add_subplot(111)
        self.axes.set(
            xlim=(settings.min_lon, settings.max_lon),
            ylim=(settings.min_lat, settings.max_lat),
            xlabel="Longitude (°)",
            ylabel="Latitude (°)",
        )
        self.axes.grid(color="0.9", linewidth=0.6)
        if contour is not None:
            self.axes.plot(*contour.T, color="0.35", linewidth=0.55)
        (self.shadow,) = self.axes.plot([], [], color="#b34130", linewidth=1, zorder=2)
        palette = LinearSegmentedColormap.from_list("maptec", ["blue", "green", "yellow", "orange", "red"])
        self.dots = self.axes.scatter(
            [],
            [],
            c=[],
            s=6 * settings.point_size,
            cmap=palette if settings.color else "gray",
            vmin=settings.color_min,
            vmax=settings.color_max,
            zorder=3,
        )
        self.figure.colorbar(self.dots, ax=self.axes, label="dI (TECU)")

    def write(self, output: Path, day: date, seconds: int, points: list[MapPoint]) -> None:
        ut = seconds / 3600
        stem = f"Map_{day.year:04d}_{day.timetuple().tm_yday:03d}_UT_{ut:8.5f}"
        (output / f"{stem}.dat").write_text("".join(point.format_row(ut) for point in points), encoding="ascii")
        self.dots.set_offsets(np.asarray([(point.longitude, point.latitude) for point in points]))
        self.dots.set_array(np.asarray([point.di for point in points]))
        self.axes.set_title(f"{day.isoformat()} · UT {ut:.5f}")
        if self.solar:
            self.shadow.set_data(*terminator(day, seconds, self.settings.shell_height_km))
        self.figure.savefig(output / f"{stem}.png")
