"""Encode one station-day of geolocated observations for downstream queries."""

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from numpy.typing import NDArray

from . import __version__
from .settings import PeriodBand, VariationSettings


@dataclass
class FilteredArc:
    """Observed samples, thin-shell geometry and per-band variations on the same grid."""

    samples: NDArray[np.float64]
    latitude: NDArray[np.float64]
    longitude: NDArray[np.float64]
    factor: NDArray[np.float64]
    variations: dict[str, NDArray[np.float64]]
    smoothed_tec: NDArray[np.float64]


def observation_schema(settings: VariationSettings, bands: tuple[PeriodBand, ...]) -> pa.Schema:
    """Define stable types, units and reproducibility metadata."""
    return pa.schema(
        [
            pa.field("datetime_utc", pa.timestamp("us", tz="UTC"), nullable=False),
            pa.field("station", pa.string(), nullable=False),
            pa.field("satellite", pa.string(), nullable=False),
            pa.field("elevation_deg", pa.float64(), nullable=False),
            pa.field("azimuth_deg", pa.float64(), nullable=False),
            pa.field("ipp_latitude_deg", pa.float64(), nullable=False),
            pa.field("ipp_longitude_deg", pa.float64(), nullable=False),
            pa.field("slant_to_vertical_factor", pa.float64(), nullable=False),
            pa.field("arc_id", pa.int32(), nullable=False),
            *(pa.field(band.column, pa.float64(), metadata={"units": "TECU"}) for band in bands),
        ],
        metadata={
            "package_version": __version__,
            "shell_height_km": str(settings.shell_height_km),
            "bands_minutes": json.dumps([[band.shortest_minutes, band.longest_minutes] for band in bands]),
            "filter": settings.filter,
            "elevation_cutoff_deg": str(settings.elevation_cutoff),
            "projection": "vertical" if settings.vertical else "slant",
            "tec_parameter": settings.tec_parameter,
            "max_absent_points": str(settings.max_absent_points),
            **(
                {"minimum_tec": str(settings.minimum_tec), "skip_edge_samples": str(settings.skip_edge_samples)}
                if settings.filter == "legacy"
                else {}
            ),
        },
    )


def observation_batch(
    day: date, site: str, satellite: str, arc_id: int, arc: FilteredArc, schema: pa.Schema
) -> pa.RecordBatch:
    """Keep each observed epoch in this UTC day, including rows with no valid band."""
    seconds, elevation, azimuth, _, _ = arc.samples.T
    in_day = (seconds >= 0) & (seconds < 86400)
    count = int(np.count_nonzero(in_day))
    midnight = np.datetime64(day, "us")
    times = midnight + np.rint(seconds[in_day] * 1_000_000).astype("timedelta64[us]")
    return pa.RecordBatch.from_arrays(
        [
            pa.array(times, type=schema.field("datetime_utc").type),
            pa.array([site] * count),
            pa.array([satellite] * count),
            pa.array(elevation[in_day]),
            pa.array(azimuth[in_day]),
            pa.array(arc.latitude[in_day]),
            pa.array(arc.longitude[in_day]),
            pa.array(arc.factor[in_day]),
            pa.array(np.full(count, arc_id, dtype=np.int32)),
            *(pa.array(values[in_day], from_pandas=True) for values in arc.variations.values()),
        ],
        schema=schema,
    )


def write_station_day(staging: Path, day: date, site: str, table: pa.Table) -> None:
    """Write a station partition ordered for time-window queries."""
    target = staging / site
    target.mkdir()
    filename = f"{site}_dtec_{day.timetuple().tm_yday:03d}_{day.year % 100:02d}.parquet"
    pq.write_table(table.sort_by([("datetime_utc", "ascending"), ("satellite", "ascending")]), target / filename)
