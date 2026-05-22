"""
tec_map_pipeline.py — TEC-suite parquet → VTEC map pipeline (ict-hub).

This module is a focused migration of the TEC map pipeline logic from
`tec-map-prototype/tec_map_pipeline.py` into ict-hub, with one key constraint:
it ONLY consumes precomputed tec-suite outputs stored in `.parquet`.

It does NOT run tec-suite, and it does NOT parse `.dat` files.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as _date
from datetime import datetime, timedelta
from pathlib import Path
import json
import math
import re
from typing import Any, Iterable

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from scipy.optimize import minimize_scalar


EARTH_RADIUS_KM = 6371.0
DEFAULT_MSTD_SEARCH_BOUNDS_TECU = (-150.0, 150.0)

# WGS84 for ECEF -> geodetic (ported from prototype)
WGS84_A_M = 6378137.0
WGS84_F = 1.0 / 298.257223563
WGS84_B_M = WGS84_A_M * (1.0 - WGS84_F)
WGS84_E2 = WGS84_F * (2.0 - WGS84_F)
WGS84_EP2 = WGS84_E2 / (1.0 - WGS84_E2)


@dataclass(frozen=True, slots=True)
class TecMapConfig:
    # Filtering / grouping
    min_elevation_deg: float = 20.0
    sampling_interval_seconds: int = 300
    frame_minutes: int = 15

    # VTEC / IPP
    ionosphere_height_km: float = 350.0

    # Gridding
    grid_resolution_deg: float = 1.0
    interpolation_method: str = "linear"
    fallback_interpolation_method: str = "nearest"
    ipp_gradient_radius_km: float | None = 300.0
    smoothing_sigma: float = 1.0
    coverage_mask_smoothing_cells: float = 0.85

    # Bias correction (ported from prototype default behavior)
    apply_mstd_receiver_bias: bool = True
    mstd_search_bounds_tecu: tuple[float, float] = DEFAULT_MSTD_SEARCH_BOUNDS_TECU

    # Per-arc temporal smoothing of vtec_tecu (rolling median window in epochs; 0 disables).
    vtec_smooth_epochs: int = 0

    # Per-station VTEC median-shift normalization mode:
    #   "off"    — never shift
    #   "auto"   — shift only stations whose MSTD receiver-bias estimate failed (rx_bias_estimated=False)
    #   "always" — shift every station to share a common high-elevation median
    normalize_stations: str = "off"
    # Only samples at elevation >= this value contribute to the per-station reference median.
    normalize_min_elevation_deg: float = 60.0

    # Output constraints (PadArt-style positivity is optional here; default on)
    enforce_nonnegative_vtec: bool = True


# ---------------------------------------------------------------------------
# Parquet normalization
# ---------------------------------------------------------------------------

_TEC_SUITE_PARQUET_HEADER_KEY = b"dat_parquet_handler.header_lines"

_DEFAULT_TEC_SUITE_MAPPING: dict[str, str] = {
    # datetime/time
    "datetime": "datetime",
    "tsn": "tsn",
    "hour": "hour",
    # receiver position
    "site.l": "site_lon",
    "site.b": "site_lat",
    "site.x": "site_x",
    "site.y": "site_y",
    "site.z": "site_z",
    # satellite position (optional)
    "sat.x": "sat_x",
    "sat.y": "sat_y",
    "sat.z": "sat_z",
    # geometry
    "el": "elevation_deg",
    "az": "azimuth_deg",
    # observables
    "tec.l1l2": "phase_tec",
    "tec.p1p2": "code_tec_p1p2",
    "tec.c1p2": "code_tec_c1p2",
    # flags
    "validity": "validity",
    # already-normalized aliases
    "elevation_deg": "elevation_deg",
    "azimuth_deg": "azimuth_deg",
    "phase_tec": "phase_tec",
    "code_tec_p1p2": "code_tec_p1p2",
    "code_tec_c1p2": "code_tec_c1p2",
    "site_lon": "site_lon",
    "site_lat": "site_lat",
    "site_x": "site_x",
    "site_y": "site_y",
    "site_z": "site_z",
    "sat_x": "sat_x",
    "sat_y": "sat_y",
    "sat_z": "sat_z",
}

_DEFAULT_READ_COLUMNS: set[str] = set(_DEFAULT_TEC_SUITE_MAPPING.keys()) | {"station", "satellite"}


def _parse_station_satellite_from_filename(path: Path) -> tuple[str | None, str | None]:
    # Matches tec-suite naming: <station>_<satellite>_<doy>_<yy>.parquet
    # Some datasets include additional suffixes (e.g. "__dup1") to avoid collisions;
    # accept those as well by matching only the leading convention portion.
    match = re.match(r"(?P<station>[A-Za-z0-9]{4})_(?P<satellite>[A-Za-z]\d{2})_\d{3}_\d{2}", path.stem)
    if match is None:
        return None, None
    return match.group("station").lower(), match.group("satellite").upper()


def _parquet_header_metadata_from_schema(schema: Any) -> dict[str, Any]:
    metadata = getattr(schema, "metadata", None) or {}
    encoded = metadata.get(_TEC_SUITE_PARQUET_HEADER_KEY)
    if not encoded:
        return {}

    try:
        header_lines = json.loads(encoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}

    if not isinstance(header_lines, list) or not all(isinstance(line, str) for line in header_lines):
        return {}

    return _parse_tecsuite_header_lines(header_lines)


def _parquet_header_metadata(path: Path) -> dict[str, Any]:
    try:
        schema = pq.read_schema(path)
    except Exception:
        return {}
    return _parquet_header_metadata_from_schema(schema)


def _parse_tecsuite_header_lines(header_lines: list[str]) -> dict[str, Any]:
    """
    Parse tec-suite DAT header lines stored in parquet metadata.

    We only extract fields used by the pipeline normalization:
      - site_lon/site_lat (degrees)
      - site_x/site_y/site_z (meters)
      - interval_seconds (used for tsn → datetime reconstruction)
      - datetime_format (used when a 'datetime' string column exists)
    """
    metadata: dict[str, Any] = {}
    float_pattern = re.compile(r"[-+]?\d+(?:\.\d+)?")

    for line in header_lines:
        if line.startswith("# Columns:"):
            raw_columns = line.split(":", 1)[1].strip()
            metadata["columns"] = [value.strip() for value in raw_columns.split(",") if value.strip()]
        elif line.startswith("# Position (L, B, H):"):
            values = [float(value) for value in float_pattern.findall(line)]
            if len(values) >= 2:
                metadata["site_lon"] = values[0]
                metadata["site_lat"] = values[1]
        elif line.startswith("# Position (X, Y, Z):"):
            values = [float(value) for value in float_pattern.findall(line)]
            if len(values) >= 3:
                metadata["site_x"] = values[0]
                metadata["site_y"] = values[1]
                metadata["site_z"] = values[2]
        elif line.lower().startswith("# interval"):
            match = float_pattern.search(line)
            if match:
                metadata["interval_seconds"] = float(match.group(0))
        elif line.startswith("# datetime format:"):
            metadata["datetime_format"] = line.split(":", 1)[1].strip()

    return metadata


def _base_date_for_year_doy(year: int, doy: int) -> pd.Timestamp:
    return pd.Timestamp(year=year, month=1, day=1) + pd.to_timedelta(doy - 1, unit="D")


def _parse_utc_timestamp(value: str) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    return ts


def _date_to_year_doy(day: _date) -> tuple[int, int]:
    return day.year, day.timetuple().tm_yday


def _coerce_datetime_column(
    frame: pd.DataFrame,
    *,
    metadata: dict[str, Any],
    year: int,
    doy: int,
) -> pd.Series:
    if "datetime" in frame.columns:
        datetime_format = metadata.get("datetime_format")
        if datetime_format:
            parsed = pd.to_datetime(frame["datetime"], format=datetime_format, errors="coerce")
        else:
            parsed = pd.to_datetime(frame["datetime"], errors="coerce")

        # tec-suite sometimes uses YYYY:DOY:seconds forms; try to recover those
        missing_mask = parsed.isna()
        if missing_mask.any():
            pattern = re.compile(r"(?P<year>\d{4}):(?P<doy>\d{3}):(?P<sec>\d+)")

            def _parse_fallback(value: object) -> pd.Timestamp | pd.NaT:
                if value is None or (isinstance(value, float) and math.isnan(value)):
                    return pd.NaT
                text = str(value).strip()
                match = pattern.fullmatch(text)
                if match is None:
                    return pd.NaT
                y = int(match.group("year"))
                d = int(match.group("doy"))
                s = int(match.group("sec"))
                base = datetime(y, 1, 1) + timedelta(days=d - 1)
                return pd.Timestamp(base) + pd.to_timedelta(s, unit="s")

            parsed.loc[missing_mask] = frame.loc[missing_mask, "datetime"].map(_parse_fallback)

        return parsed

    base_date = _base_date_for_year_doy(year, doy)
    if "tsn" in frame.columns:
        interval_seconds = metadata.get("interval_seconds")
        if interval_seconds is not None:
            return base_date + pd.to_timedelta(frame["tsn"].astype(float) * float(interval_seconds), unit="s")

    if "hour" in frame.columns:
        return base_date + pd.to_timedelta(frame["hour"].astype(float), unit="h")

    raise RuntimeError(
        "Unable to reconstruct datetime for TEC records: missing 'datetime' and 'tsn'/'hour' columns."
    )


def normalize_tecsuite_parquet_frame(
    *,
    frame: pd.DataFrame,
    metadata: dict[str, Any],
    year: int,
    doy: int,
    station: str | None,
    satellite: str | None,
    source_file: Path,
    column_mapping: dict[str, str] | None = None,
) -> pd.DataFrame:
    mapping = column_mapping or _DEFAULT_TEC_SUITE_MAPPING

    normalized = frame.rename(columns=mapping).copy()
    normalized["datetime"] = _coerce_datetime_column(normalized, metadata=metadata, year=year, doy=doy)

    for name in ("site_lon", "site_lat", "site_x", "site_y", "site_z", "elevation_deg", "azimuth_deg"):
        if name not in normalized.columns:
            normalized[name] = pd.Series(index=normalized.index, dtype=float)

    if "site_lon" in metadata:
        normalized["site_lon"] = normalized["site_lon"].fillna(float(metadata["site_lon"]))
    if "site_lat" in metadata:
        normalized["site_lat"] = normalized["site_lat"].fillna(float(metadata["site_lat"]))
    if "site_x" in metadata:
        normalized["site_x"] = normalized["site_x"].fillna(float(metadata["site_x"]))
    if "site_y" in metadata:
        normalized["site_y"] = normalized["site_y"].fillna(float(metadata["site_y"]))
    if "site_z" in metadata:
        normalized["site_z"] = normalized["site_z"].fillna(float(metadata["site_z"]))

    normalized["code_tec_p1p2"] = normalized.get(
        "code_tec_p1p2",
        pd.Series(index=normalized.index, dtype=float),
    ).fillna(0.0)
    normalized["code_tec_c1p2"] = normalized.get(
        "code_tec_c1p2",
        pd.Series(index=normalized.index, dtype=float),
    ).fillna(0.0)
    normalized["validity"] = normalized.get(
        "validity",
        pd.Series(index=normalized.index, dtype=float),
    ).fillna(0).astype(int)

    if "phase_tec" not in normalized.columns:
        raise RuntimeError(f"{source_file} missing required TEC observable column: phase_tec / tec.l1l2")

    if normalized["site_lon"].isna().all() or normalized["site_lat"].isna().all():
        # If ECEF exists, derive lon/lat.
        if not (normalized["site_x"].isna().all() or normalized["site_y"].isna().all() or normalized["site_z"].isna().all()):
            lats, lons, _alts = zip(
                *[
                    ecef_to_geodetic(float(x), float(y), float(z))
                    for x, y, z in zip(
                        normalized["site_x"].astype(float).to_numpy(),
                        normalized["site_y"].astype(float).to_numpy(),
                        normalized["site_z"].astype(float).to_numpy(),
                        strict=False,
                    )
                ],
                strict=False,
            )
            normalized["site_lat"] = pd.Series(lats, index=normalized.index, dtype=float)
            normalized["site_lon"] = pd.Series(lons, index=normalized.index, dtype=float)
        else:
            raise RuntimeError(
                f"{source_file} does not provide station longitude/latitude (site_lon/site_lat) "
                "and does not provide site_x/site_y/site_z to derive them."
            )

    if station is None:
        if "station" in normalized.columns and normalized["station"].notna().any():
            normalized["station"] = normalized["station"].astype("string").str.lower()
        else:
            inferred, _ = _parse_station_satellite_from_filename(source_file)
            station = inferred
            if station is None:
                raise RuntimeError(
                    f"Unable to infer station for {source_file.name}; provide a 'station' column or name by tec-suite convention."
                )
            normalized["station"] = str(station).lower()
    else:
        normalized["station"] = str(station).lower()

    if satellite is None:
        if "satellite" in normalized.columns and normalized["satellite"].notna().any():
            normalized["satellite"] = normalized["satellite"].astype("string").str.upper()
        else:
            _, inferred = _parse_station_satellite_from_filename(source_file)
            satellite = inferred
            if satellite is None:
                raise RuntimeError(
                    f"Unable to infer satellite for {source_file.name}; provide a 'satellite' column or name by tec-suite convention."
                )
            normalized["satellite"] = str(satellite)
    else:
        normalized["satellite"] = str(satellite)
    normalized["day_of_year"] = int(doy)
    normalized["year"] = int(year)
    normalized["source_file"] = str(source_file)

    # Optional ECEF columns
    for name in ("sat_x", "sat_y", "sat_z"):
        if name not in normalized.columns:
            continue
        normalized[name] = pd.to_numeric(normalized[name], errors="coerce")

    required_columns = [
        "datetime",
        "site_lon",
        "site_lat",
        "elevation_deg",
        "azimuth_deg",
        "phase_tec",
        "code_tec_p1p2",
        "code_tec_c1p2",
        "validity",
        "station",
        "satellite",
        "day_of_year",
        "year",
        "source_file",
    ]
    for col in required_columns:
        if col not in normalized.columns:
            raise RuntimeError(f"{source_file} is missing required normalized column: {col}")

    return normalized[required_columns + [c for c in ("site_x", "site_y", "site_z", "sat_x", "sat_y", "sat_z") if c in normalized.columns]]


def _iter_station_day_parquet_files(root: Path, year: int, doy: int, station: str) -> Iterable[Path]:
    root = Path(root)
    station = station.lower()
    day_dir = root / str(year) / f"{doy:03d}"
    candidates = [
        day_dir / station,
        day_dir / f"{station}{doy:03d}0",
    ]
    for station_dir in candidates:
        if not station_dir.exists() or not station_dir.is_dir():
            continue
        selected: dict[str, Path] = {}
        for path in sorted(station_dir.glob("*.parquet")):
            canonical_stem = re.sub(r"__dup\d+$", "", path.stem, flags=re.IGNORECASE)
            current = selected.get(canonical_stem)
            if current is None:
                selected[canonical_stem] = path
                continue
            # Prefer the unsuffixed shard when both canonical and "__dupN" variants exist.
            current_has_dup_suffix = bool(re.search(r"__dup\d+$", current.stem, flags=re.IGNORECASE))
            path_has_dup_suffix = bool(re.search(r"__dup\d+$", path.stem, flags=re.IGNORECASE))
            if current_has_dup_suffix and not path_has_dup_suffix:
                selected[canonical_stem] = path
        for path in selected.values():
            yield path


def load_tecs_parquet(
    *,
    root: Path,
    year: int | None = None,
    doy: int | None = None,
    date: str | None = None,
    stations: list[str],
    start_time: str | None = None,
    end_time: str | None = None,
    timestamp: str | None = None,
    min_elevation_deg: float = 20.0,
    column_mapping: dict[str, str] | None = None,
) -> pd.DataFrame:
    """
    Load raw tec-suite links from parquet, normalize into internal schema, and filter.

    Time selection:
      - Provide `timestamp` (snapshot) OR (`start_time` and `end_time`) (range).
      - `start_time`, `end_time`, and `timestamp` accept either:
          * full ISO timestamps, e.g. "2026-01-01T02:30:00Z"
          * clock strings "HH:MM:SS" (combined with provided `date`/`year`+`doy`)
    """
    if date:
        y, d = _date_to_year_doy(pd.Timestamp(date).date())
        year = y
        doy = d
    if year is None or doy is None:
        raise ValueError("Either `date` or (`year` and `doy`) must be provided.")

    station_list = [str(s).lower() for s in stations if str(s).strip()]
    if not station_list:
        raise ValueError("At least one station must be provided.")

    if timestamp and (start_time or end_time):
        raise ValueError("Provide either `timestamp` OR (`start_time` and `end_time`), not both.")

    base_date = _base_date_for_year_doy(int(year), int(doy))

    def _combine_clock(clock_text: str) -> pd.Timestamp:
        parts = clock_text.split(":")
        if len(parts) != 3:
            raise ValueError(f"Clock value must be HH:MM:SS, got: {clock_text}")
        hh, mm, ss = (int(v) for v in parts)
        return base_date + pd.to_timedelta(hh * 3600 + mm * 60 + ss, unit="s")

    clock_mode = False
    if timestamp:
        ts = _parse_utc_timestamp(timestamp) if ("T" in timestamp or " " in timestamp) else _combine_clock(timestamp)
        start_dt = ts
        end_dt = ts
    else:
        if not (start_time and end_time):
            raise ValueError("Range mode requires both `start_time` and `end_time`.")
        start_is_clock = ("T" not in start_time and " " not in start_time)
        end_is_clock = ("T" not in end_time and " " not in end_time)
        clock_mode = start_is_clock and end_is_clock
        start_dt = _parse_utc_timestamp(start_time) if not start_is_clock else _combine_clock(start_time)
        end_dt = _parse_utc_timestamp(end_time) if not end_is_clock else _combine_clock(end_time)

    rows: list[pd.DataFrame] = []
    for station in station_list:
        for path in _iter_station_day_parquet_files(Path(root), int(year), int(doy), station):
            schema = None
            available_columns: set[str] | None = None
            try:
                schema = pq.read_schema(path)
                available_columns = set(getattr(schema, "names", []))
            except Exception:
                schema = None
                available_columns = None

            columns = None
            if available_columns:
                columns = sorted(available_columns.intersection(_DEFAULT_READ_COLUMNS))

            meta = _parquet_header_metadata_from_schema(schema) if schema is not None else {}

            filters: list[tuple[str, str, object]] | list[list[tuple[str, str, object]]] | None = None
            try:
                start_seconds = float((pd.Timestamp(start_dt) - pd.Timestamp(base_date)).total_seconds())
                end_seconds = float((pd.Timestamp(end_dt) - pd.Timestamp(base_date)).total_seconds())
                wraps = bool(clock_mode and pd.Timestamp(start_dt) > pd.Timestamp(end_dt))

                interval_seconds = meta.get("interval_seconds")
                if available_columns and "tsn" in available_columns and interval_seconds:
                    dt_seconds = float(interval_seconds)
                    start_tsn = int(math.floor(start_seconds / dt_seconds))
                    end_tsn = int(math.ceil(end_seconds / dt_seconds))
                    if wraps:
                        filters = [[("tsn", ">=", start_tsn)], [("tsn", "<=", end_tsn)]]
                    else:
                        filters = [("tsn", ">=", start_tsn), ("tsn", "<=", end_tsn)]
                elif available_columns and "hour" in available_columns:
                    start_hour = start_seconds / 3600.0
                    end_hour = end_seconds / 3600.0
                    if wraps:
                        filters = [[("hour", ">=", start_hour)], [("hour", "<=", end_hour)]]
                    else:
                        filters = [("hour", ">=", start_hour), ("hour", "<=", end_hour)]
            except Exception:
                filters = None

            try:
                if filters is None:
                    df = pd.read_parquet(path, engine="pyarrow", columns=columns)
                else:
                    try:
                        df = pd.read_parquet(path, engine="pyarrow", columns=columns, filters=filters)
                    except Exception:
                        df = pd.read_parquet(path, engine="pyarrow", columns=columns)
            except Exception as exc:
                raise RuntimeError(f"Failed reading parquet: {path} ({exc})") from exc

            if df.empty:
                continue

            normalized = normalize_tecsuite_parquet_frame(
                frame=df,
                metadata=meta,
                year=int(year),
                doy=int(doy),
                station=None,
                satellite=None,
                source_file=path,
                column_mapping=column_mapping,
            )
            rows.append(normalized)

    if not rows:
        raise RuntimeError("No usable tec-suite parquet files were found for the requested selection.")

    raw_links = pd.concat(rows, ignore_index=True)
    raw_links["datetime"] = pd.to_datetime(raw_links["datetime"], errors="coerce")
    raw_links = raw_links[raw_links["datetime"].notna()].copy()
    raw_links = raw_links.sort_values(["station", "satellite", "datetime"]).reset_index(drop=True)

    # Filter by time selection.
    # - ISO timestamps: absolute inclusive filter.
    # - Clock strings: match prototype semantics (apply_time_window), including wrap-around.
    if clock_mode:
        start_clock = pd.Timestamp(start_dt).time()
        end_clock = pd.Timestamp(end_dt).time()
        clock = raw_links["datetime"].dt.time
        if start_clock <= end_clock:
            mask = (clock >= start_clock) & (clock <= end_clock)
        else:
            mask = (clock >= start_clock) | (clock <= end_clock)
        raw_links = raw_links[mask].copy()
    else:
        raw_links = raw_links[(raw_links["datetime"] >= start_dt) & (raw_links["datetime"] <= end_dt)].copy()

    # Elevation filter consistent with prototype (leveled_slm path)
    raw_links = raw_links[raw_links["elevation_deg"] >= float(min_elevation_deg)].copy()
    return raw_links


# ---------------------------------------------------------------------------
# Transform pipeline (ported from prototype: leveled_slm path)
# ---------------------------------------------------------------------------

def select_code_tec(group: pd.DataFrame) -> pd.Series:
    preferred = group["code_tec_p1p2"].where(group["code_tec_p1p2"].abs() > 1e-3)
    fallback = group["code_tec_c1p2"].where(group["code_tec_c1p2"].abs() > 1e-3)
    return preferred.fillna(fallback)


def level_phase_group(group: pd.DataFrame, sampling_interval_seconds: int) -> pd.DataFrame:
    group = group.sort_values("datetime").copy()
    group["code_tec"] = select_code_tec(group)

    phase_ok = group["phase_tec"].abs() > 1e-3
    code_ok = group["code_tec"].notna()
    gap = group["datetime"].diff().dt.total_seconds().fillna(0) > sampling_interval_seconds * 1.5
    reset = gap | (group["validity"] != 0) | (~phase_ok)
    group["arc_id"] = reset.cumsum()
    group["stec_tecu"] = group["code_tec"]

    # Vectorized phase-to-code leveling per arc:
    # - For each arc with >=3 "good" samples, compute median(code_tec - phase_tec)
    # - STEC = phase_tec + bias for all rows in that arc
    good_mask = phase_ok & code_ok
    if bool(good_mask.any()):
        good = group.loc[good_mask, ["arc_id", "code_tec", "phase_tec"]].copy()
        good["delta"] = good["code_tec"] - good["phase_tec"]
        bias_by_arc = good.groupby("arc_id")["delta"].median()
        count_by_arc = good.groupby("arc_id")["delta"].size()
        eligible_arcs = set(bias_by_arc.index[count_by_arc >= 3].tolist())
        if eligible_arcs:
            bias_map = bias_by_arc.to_dict()
            arc_bias = group["arc_id"].map(bias_map)
            eligible_mask = group["arc_id"].isin(eligible_arcs) & group["phase_tec"].notna()
            group.loc[eligible_mask, "stec_tecu"] = group.loc[eligible_mask, "phase_tec"] + arc_bias.loc[eligible_mask]

    return group


def mapping_function(elevation_deg: pd.Series, ionosphere_height_km: float) -> np.ndarray:
    elevation_rad = np.deg2rad(elevation_deg.to_numpy())
    ratio = EARTH_RADIUS_KM / (EARTH_RADIUS_KM + float(ionosphere_height_km))
    sin_chi = np.clip(ratio * np.cos(elevation_rad), -1.0, 1.0)
    return 1.0 / np.sqrt(1.0 - sin_chi**2)


def ipp_coordinates(
    site_lat_deg: pd.Series,
    site_lon_deg: pd.Series,
    elevation_deg: pd.Series,
    azimuth_deg: pd.Series,
    ionosphere_height_km: float,
) -> tuple[np.ndarray, np.ndarray]:
    lat = np.deg2rad(site_lat_deg.to_numpy())
    lon = np.deg2rad(site_lon_deg.to_numpy())
    el = np.deg2rad(elevation_deg.to_numpy())
    az = np.deg2rad(azimuth_deg.to_numpy())

    psi = np.pi / 2.0 - el - np.arcsin(
        np.clip(
            EARTH_RADIUS_KM / (EARTH_RADIUS_KM + float(ionosphere_height_km)) * np.cos(el),
            -1.0,
            1.0,
        )
    )

    ipp_lat = np.arcsin(np.sin(lat) * np.cos(psi) + np.cos(lat) * np.sin(psi) * np.cos(az))
    ipp_lon = lon + np.arctan2(
        np.sin(psi) * np.sin(az),
        np.cos(lat) * np.cos(psi) - np.sin(lat) * np.sin(psi) * np.cos(az),
    )

    ipp_lat_deg = np.rad2deg(ipp_lat)
    ipp_lon_deg = (np.rad2deg(ipp_lon) + 540.0) % 360.0 - 180.0
    return ipp_lat_deg, ipp_lon_deg


def build_leveled_links(raw_links: pd.DataFrame, config: TecMapConfig) -> pd.DataFrame:
    if raw_links.empty:
        raise RuntimeError("No raw link data is available after loading/parquet filtering.")

    groups: list[pd.DataFrame] = []
    for _, group in raw_links.groupby(["station", "satellite"], sort=False):
        groups.append(level_phase_group(group, config.sampling_interval_seconds))

    leveled_links = pd.concat(groups, ignore_index=True)
    leveled_links = leveled_links[leveled_links["stec_tecu"].notna()].copy()
    leveled_links["mapping_factor"] = mapping_function(leveled_links["elevation_deg"], config.ionosphere_height_km)
    leveled_links["stec_tecu_raw"] = leveled_links["stec_tecu"]
    leveled_links["vtec_tecu_raw"] = leveled_links["stec_tecu_raw"] / leveled_links["mapping_factor"]

    ipp_lat, ipp_lon = ipp_coordinates(
        leveled_links["site_lat"],
        leveled_links["site_lon"],
        leveled_links["elevation_deg"],
        leveled_links["azimuth_deg"],
        config.ionosphere_height_km,
    )
    leveled_links["ipp_lat"] = ipp_lat
    leveled_links["ipp_lon"] = ipp_lon

    leveled_links = apply_bias_corrections(leveled_links, config)
    leveled_links["vtec_tecu"] = leveled_links["stec_tecu"] / leveled_links["mapping_factor"]

    # Per-arc temporal smoothing (noise reduction; does not address absolute calibration).
    if int(getattr(config, "vtec_smooth_epochs", 0) or 0) > 0:
        leveled_links = smooth_vtec_temporal(leveled_links, int(config.vtec_smooth_epochs))

    # Per-station median-shift normalisation (compensates for the silent MSTD-NaN path).
    leveled_links = normalize_station_offsets(
        leveled_links,
        mode=getattr(config, "normalize_stations", "off"),
        reference_min_elevation_deg=float(getattr(config, "normalize_min_elevation_deg", 60.0)),
    )

    if config.enforce_nonnegative_vtec:
        leveled_links["vtec_tecu"] = leveled_links["vtec_tecu"].clip(lower=0.0)
    leveled_links = leveled_links[leveled_links["vtec_tecu"].between(0 if config.enforce_nonnegative_vtec else -200, 200)].copy()
    return leveled_links


def smooth_vtec_temporal(leveled_links: pd.DataFrame, window_epochs: int) -> pd.DataFrame:
    """
    Rolling-median smoothing of vtec_tecu inside each (station, satellite, arc_id) group.

    Does not cross arc boundaries (so it does not fight the phase-leveling) and does
    not span stations (so it does not smear any genuine inter-station offsets). Use
    this to suppress per-epoch jitter that would otherwise turn into spurious gradient
    features after spatial differentiation.
    """
    if window_epochs <= 0 or leveled_links.empty:
        return leveled_links
    if "arc_id" not in leveled_links.columns:
        return leveled_links

    out = leveled_links.sort_values(["station", "satellite", "arc_id", "datetime"]).copy()
    out["vtec_tecu"] = (
        out.groupby(["station", "satellite", "arc_id"], sort=False, group_keys=False)["vtec_tecu"]
        .transform(lambda s: s.rolling(window=int(window_epochs), center=True, min_periods=1).median())
    )
    return out


def normalize_station_offsets(
    leveled_links: pd.DataFrame,
    *,
    mode: str,
    reference_min_elevation_deg: float = 60.0,
) -> pd.DataFrame:
    """
    Per-station median-shift of vtec_tecu so all (selected) stations share the
    cohort's high-elevation median for the loaded window.

    mode:
      "off"    — return input unchanged.
      "auto"   — only shift stations whose MSTD receiver-bias estimate failed
                  (rx_bias_estimated is False for some/all of their rows). Stations
                  with a successful MSTD estimate are left untouched.
      "always" — shift every station to the cohort median.

    Reference samples use elevation >= reference_min_elevation_deg, where the
    obliquity mapping factor is ~1 and per-station VTEC means reflect mostly
    the absolute calibration of the receiver.

    The applied shift is recorded in a new column ``station_offset_applied_tecu``.
    """
    if leveled_links.empty:
        return leveled_links
    mode = (mode or "off").strip().lower()
    if mode == "off":
        return leveled_links

    out = leveled_links.copy()
    out["station_offset_applied_tecu"] = 0.0

    high_el = out[out["elevation_deg"] >= float(reference_min_elevation_deg)]
    if high_el.empty:
        return out

    cohort_median = float(high_el["vtec_tecu"].median())
    station_medians = high_el.groupby("station")["vtec_tecu"].median()
    offsets = station_medians - cohort_median

    if mode == "auto":
        if "rx_bias_estimated" not in out.columns:
            return out
        # A station is "MSTD-failed" if any of its rows have rx_bias_estimated == False.
        # In practice MSTD success/failure is per (station, day, code-pair) — so a partial
        # failure still leaves uncalibrated samples, and we should normalise the whole station.
        failed_stations = set(
            out.loc[~out["rx_bias_estimated"].astype(bool), "station"].unique()
        )
        offsets = offsets.loc[offsets.index.isin(failed_stations)]
    elif mode != "always":
        return out

    if offsets.empty:
        return out

    shift = out["station"].map(offsets.to_dict()).fillna(0.0).astype(float)
    out["vtec_tecu"] = out["vtec_tecu"] - shift
    out["station_offset_applied_tecu"] = shift
    return out


def build_frame_summary(leveled_links: pd.DataFrame, config: TecMapConfig) -> pd.DataFrame:
    if leveled_links.empty:
        raise RuntimeError("No leveled link data is available after filtering and phase-to-code leveling.")

    summary = leveled_links.copy()
    summary["frame_time"] = summary["datetime"].dt.floor(f"{int(config.frame_minutes)}min")
    frame_summary = (
        summary.groupby(["frame_time", "station"], as_index=False)
        .agg(
            site_lat=("site_lat", "first"),
            site_lon=("site_lon", "first"),
            ipp_lat=("ipp_lat", "mean"),
            ipp_lon=("ipp_lon", "mean"),
            vtec_tecu=("vtec_tecu", "mean"),
            samples=("vtec_tecu", "size"),
        )
        .sort_values(["frame_time", "station"])
        .reset_index(drop=True)
    )

    # Defensive: keep PadArt-style positivity invariant at the frame level too.
    # (Interpolation/smoothing should not be able to introduce negatives if inputs are >= 0,
    # but frame aggregation can still be impacted by upstream changes.)
    if config.enforce_nonnegative_vtec and not frame_summary.empty:
        frame_summary["vtec_tecu"] = frame_summary["vtec_tecu"].clip(lower=0.0)

    if frame_summary.empty:
        raise RuntimeError("Frame summary is empty; widen the time window or relax the filters.")

    return frame_summary


# ---------------------------------------------------------------------------
# Bias correction (ported from prototype: leveled_slm path)
# ---------------------------------------------------------------------------

def apply_bias_corrections(leveled_links: pd.DataFrame, config: TecMapConfig) -> pd.DataFrame:
    """
    Apply bias corrections consistent with the prototype's default configuration.

    Implemented:
      - MSTD receiver bias estimation for GPS code combinations (C1W/C2W or C1C/C2W),
        derived from which code TEC observable was used for leveling.

    Not implemented in ict-hub (out of scope):
      - external DCB/RINEX bias files
      - transmitter bias catalogs
    """
    leveled_links = leveled_links.copy()
    leveled_links["tx_bias_tecu"] = 0.0
    leveled_links["rx_bias_tecu"] = 0.0
    # Tracks which rows got a finite MSTD receiver-bias estimate; the normaliser
    # uses this to find stations that silently fell through to rx_bias=0.
    leveled_links["rx_bias_estimated"] = False

    leveled_links = annotate_tecsuite_bias_metadata(leveled_links)
    if config.apply_mstd_receiver_bias:
        leveled_links = apply_mstd_receiver_biases(leveled_links, bounds=config.mstd_search_bounds_tecu)

    leveled_links["stec_tecu"] = (
        leveled_links["stec_tecu_raw"]
        - leveled_links["tx_bias_tecu"].fillna(0.0)
        - leveled_links["rx_bias_tecu"].fillna(0.0)
    )
    return leveled_links


def annotate_tecsuite_bias_metadata(leveled_links: pd.DataFrame) -> pd.DataFrame:
    leveled_links = leveled_links.copy()
    if "constellation" not in leveled_links.columns:
        leveled_links["constellation"] = leveled_links["satellite"].astype(str).str[0]

    p1p2_mask = leveled_links.get(
        "code_tec_p1p2",
        pd.Series(0.0, index=leveled_links.index),
    ).abs() > 1e-3
    c1p2_mask = (
        ~p1p2_mask
        & leveled_links.get(
            "code_tec_c1p2",
            pd.Series(0.0, index=leveled_links.index),
        ).abs().fillna(0.0).gt(1e-3)
    )
    gps_mask = leveled_links["constellation"].astype(str).str.upper() == "G"

    if "c1_code" not in leveled_links.columns:
        leveled_links["c1_code"] = pd.Series(pd.NA, index=leveled_links.index, dtype="string")
    if "c2_code" not in leveled_links.columns:
        leveled_links["c2_code"] = pd.Series(pd.NA, index=leveled_links.index, dtype="string")
    if "f1_hz" not in leveled_links.columns:
        leveled_links["f1_hz"] = np.nan
    if "f2_hz" not in leveled_links.columns:
        leveled_links["f2_hz"] = np.nan

    gps_p1p2 = gps_mask & p1p2_mask
    gps_c1p2 = gps_mask & c1p2_mask
    leveled_links.loc[gps_p1p2, "c1_code"] = "C1W"
    leveled_links.loc[gps_p1p2, "c2_code"] = "C2W"
    leveled_links.loc[gps_c1p2, "c1_code"] = "C1C"
    leveled_links.loc[gps_c1p2, "c2_code"] = "C2W"
    leveled_links.loc[gps_mask & (p1p2_mask | c1p2_mask), "f1_hz"] = 1575.42e6
    leveled_links.loc[gps_mask & (p1p2_mask | c1p2_mask), "f2_hz"] = 1227.60e6
    return leveled_links


def apply_mstd_receiver_biases(
    leveled_links: pd.DataFrame,
    *,
    bounds: tuple[float, float] = DEFAULT_MSTD_SEARCH_BOUNDS_TECU,
) -> pd.DataFrame:
    leveled_links = leveled_links.copy()
    leveled_links["bias_date"] = pd.to_datetime(leveled_links["datetime"]).dt.normalize()
    grouping_columns = ["bias_date", "station", "constellation", "c1_code", "c2_code"]

    for _, group in leveled_links.groupby(grouping_columns, sort=False, dropna=False):
        if group["constellation"].isna().all() or group["c1_code"].isna().all() or group["c2_code"].isna().all():
            continue
        bias = estimate_receiver_bias_mstd_group(group, bounds=bounds)
        if np.isfinite(bias):
            leveled_links.loc[group.index, "rx_bias_tecu"] = bias
            leveled_links.loc[group.index, "rx_bias_estimated"] = True

    return leveled_links.drop(columns=["bias_date"])


def estimate_receiver_bias_mstd_group(
    group: pd.DataFrame,
    *,
    bounds: tuple[float, float] = DEFAULT_MSTD_SEARCH_BOUNDS_TECU,
    min_unique_satellites: int = 4,
    min_epochs_with_min_satellites: int = 10,
    min_satellites_per_epoch: int = 3,
) -> float:
    local_time = pd.to_datetime(group["datetime"]) + pd.to_timedelta(group["ipp_lon"] / 15.0, unit="h")
    local_hours = (
        local_time.dt.hour
        + local_time.dt.minute / 60.0
        + local_time.dt.second / 3600.0
        + local_time.dt.microsecond / 3_600_000_000.0
    )
    nighttime = (local_hours >= 18.0) | (local_hours <= 6.0)
    sample = group.loc[nighttime].copy()
    if len(sample) < 10:
        return float("nan")
    if "satellite" not in sample.columns or sample["satellite"].nunique() < min_unique_satellites:
        return float("nan")
    counts_by_epoch = sample.groupby("datetime")["satellite"].nunique()
    if int((counts_by_epoch >= min_satellites_per_epoch).sum()) < min_epochs_with_min_satellites:
        return float("nan")

    stec_without_rx = sample["stec_tecu_raw"].to_numpy(dtype=float) - sample["tx_bias_tecu"].to_numpy(dtype=float)
    mapping_factor = sample["mapping_factor"].to_numpy(dtype=float)
    frame_time = pd.to_datetime(sample["datetime"]).to_numpy(dtype="datetime64[ns]")
    # Precompute epoch groups once to avoid rebuilding DataFrames inside the optimizer.
    epoch_ids, inverse = np.unique(frame_time, return_inverse=True)
    counts = np.bincount(inverse)
    valid_epochs = counts >= 2
    if not bool(valid_epochs.any()):
        return float("nan")

    def mean_std(candidate_bias: float) -> float:
        vtec = (stec_without_rx - candidate_bias) / mapping_factor
        # Compute per-epoch std using sum and sumsq.
        sum_v = np.bincount(inverse, weights=vtec)
        sum_v2 = np.bincount(inverse, weights=vtec * vtec)
        with np.errstate(divide="ignore", invalid="ignore"):
            mean_v = sum_v / counts
            var = (sum_v2 / counts) - mean_v * mean_v
        var = np.where(var < 0, 0.0, var)
        std = np.sqrt(var)
        std = std[valid_epochs]
        std = std[np.isfinite(std)]
        if std.size == 0:
            return float("inf")
        return float(std.mean())

    result = minimize_scalar(mean_std, bounds=bounds, method="bounded")
    if not result.success:
        return float("nan")
    return float(result.x)


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def ecef_to_geodetic(x_m: float, y_m: float, z_m: float) -> tuple[float, float, float]:
    p = math.hypot(x_m, y_m)
    if p < 1e-9:
        lon = 0.0
    else:
        lon = math.atan2(y_m, x_m)
    theta = math.atan2(z_m * WGS84_A_M, p * WGS84_B_M)
    sin_theta = math.sin(theta)
    cos_theta = math.cos(theta)
    lat = math.atan2(
        z_m + WGS84_EP2 * WGS84_B_M * sin_theta**3,
        p - WGS84_E2 * WGS84_A_M * cos_theta**3,
    )
    sin_lat = math.sin(lat)
    radius = WGS84_A_M / math.sqrt(1.0 - WGS84_E2 * sin_lat**2)
    alt = p / max(math.cos(lat), 1e-12) - radius
    return math.degrees(lat), math.degrees(lon), alt
