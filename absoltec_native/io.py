"""tec-suite DAT input and AbsolTEC's eight-column TEC output.

Format facts come from gnss-lab/tec-suite ``tecs/dio/text.py``,
``tecs/validity.py`` and ``tecs/__main__.py`` at commit
18465f906a3176a2eaf130e872bfd4b650dc2721. This module does not estimate TEC.
In particular, zero TEC is a valid value; missing measurements are identified
by the producer's validity bits rather than by their numeric placeholder.
"""

from __future__ import annotations

import math
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
from numpy.typing import NDArray

TEC_COLUMNS = ("UT", "I_v", "G_lon", "G_lat", "G_q_lon", "G_q_lat", "G_t", "G_q_t")
_REQUIRED_COLUMNS = {"tsn", "hour", "el", "az", "tec.l1l2", "validity"}
_CODE_COLUMNS = {"tec.c1p2", "tec.p1p2"}
_FILE_NAME = re.compile(
    r"(?P<station>[a-z0-9]{4})_(?P<satellite>[a-z]\d{2,3})_(?P<day>\d{3})_"
    r"(?P<year>\d{4}|\d{2})(?:__dup\d+)?\.dat",
    re.IGNORECASE,
)
_SATELLITE = re.compile(r"(?P<id>[a-z]\d{2,3})(?:\s*\(k\s*=\s*(?P<channel>[+-]?\d+(?:\.\d+)?)\))?", re.IGNORECASE)
_LBH = re.compile(r"Position\s*\(\s*L\s*,\s*B\s*,\s*H\s*\)\s*:\s*(.*)", re.IGNORECASE)


class DatFormatError(ValueError):
    """A malformed input file, with its source path and line number."""


@dataclass(frozen=True)
class DatDiagnostics:
    data_rows: int
    kept_rows: int
    skipped_rows: int
    missing_phase_rows: int
    missing_code_rows: int
    cycle_slip_rows: int
    gaps: int
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class SatelliteData:
    source: Path
    satellite_id: str
    station: str
    longitude_deg: float | None
    latitude_deg: float | None
    height_m: float | None
    glonass_channel: int | None
    interval_seconds: float | None
    sampling_interval_seconds: float | None
    year: int | None
    day_of_year: int | None
    code_combination: str
    tsn: NDArray[np.int64]
    hour: NDArray[np.float64]
    elevation: NDArray[np.float64]
    azimuth: NDArray[np.float64]
    phase_tec: NDArray[np.float64]
    code_tec: NDArray[np.float64]
    validity: NDArray[np.int64]
    phase_valid: NDArray[np.bool_]
    code_valid: NDArray[np.bool_]
    break_before: NDArray[np.bool_]
    diagnostics: DatDiagnostics


def _number(text: str) -> float:
    value = float(text.replace("D", "E").replace("d", "e"))
    if not math.isfinite(value):
        raise ValueError("non-finite numeric value")
    return value


def _columns(text: str) -> tuple[str, ...]:
    return tuple(part.strip().lower() for part in text.split(",")) if "," in text else tuple(text.lower().split())


def _error(path: Path, line: int, message: str) -> DatFormatError:
    return DatFormatError(f"{path}:{line}: {message}")


def read_dat(path: str | Path, *, invalid_rows: Literal["error", "skip"] = "error") -> SatelliteData:
    """Read one satellite file, retaining missing-observation and slip flags.

    Malformed rows and repeated/non-increasing epochs raise by default. The
    explicit ``invalid_rows='skip'`` mode counts them and records diagnostics;
    a rejected row forces an arc break before the next accepted observation.
    No elevation cutoff or TEC value filtering is applied. Longitude/latitude
    are in degrees, elevation/azimuth in degrees, and time in UT hours.
    """
    source = Path(path)
    if invalid_rows not in {"error", "skip"}:
        raise ValueError("invalid_rows must be 'error' or 'skip'")
    name = _FILE_NAME.fullmatch(source.name)
    station = name["station"].lower() if name else None
    satellite_id = name["satellite"].upper() if name else None
    # A two-digit filename alone cannot resolve its century.
    year = int(name["year"]) if name and len(name["year"]) == 4 else None
    day = int(name["day"]) if name else None
    channel = None
    position = None
    interval = sampling = None
    columns = None
    code_column = None
    records: list[list[float]] = []
    forced_breaks: list[bool] = []
    issues: list[str] = []
    data_rows = skipped = 0
    force_break = True
    last_hour = last_tsn = -1.0

    # Latin-1 is lossless for legacy comments; numeric fields remain strict.
    with source.open(encoding="latin-1") as stream:
        for line_number, raw in enumerate(stream, 1):
            text = raw.strip().lstrip("\ufeff")
            if not text:
                continue
            header = text.removeprefix("#").strip()
            if text.startswith("#"):
                try:
                    if header.lower().startswith("satellite:"):
                        match = _SATELLITE.fullmatch(header.partition(":")[2].strip())
                        if not match:
                            raise ValueError("unsupported satellite header")
                        found = match["id"].upper()
                        if satellite_id is not None and found != satellite_id:
                            raise ValueError("satellite header disagrees with filename or earlier header")
                        satellite_id = found
                        if match["channel"] is not None:
                            channel_value = _number(match["channel"])
                            if not channel_value.is_integer():
                                raise ValueError("GLONASS frequency channel must be an integer")
                            new_channel = int(channel_value)
                            if channel is not None and new_channel != channel:
                                raise ValueError("conflicting GLONASS frequency channels")
                            channel = new_channel
                    elif header.lower().startswith("site:"):
                        found = header.partition(":")[2].strip().lower()
                        if not re.fullmatch(r"[a-z0-9]{4}", found):
                            raise ValueError("site must be a four-character station code")
                        if station is not None and station != found:
                            raise ValueError("site header disagrees with filename or earlier header")
                        station = found
                    elif match := _LBH.fullmatch(header):
                        found_position = tuple(_number(v.strip()) for v in match[1].split(","))
                        if len(found_position) != 3:
                            raise ValueError("expected longitude, latitude, height")
                        if not -180 <= found_position[0] <= 360 or not -90 <= found_position[1] <= 90:
                            raise ValueError("station longitude or latitude is out of range")
                        if position is not None and position != found_position:
                            raise ValueError("conflicting station positions")
                        position = found_position
                    elif header.lower().startswith("interval:"):
                        interval = _number(header.partition(":")[2].strip())
                        if interval <= 0:
                            raise ValueError("observation interval must be positive")
                    elif header.lower().startswith("sampling interval:"):
                        # tec-suite may append '(not used).' to this value.
                        sampling = _number(header.partition(":")[2].split()[0])
                        if sampling < 0:
                            raise ValueError("sampling interval cannot be negative")
                    elif header.lower().startswith("columns:"):
                        new_columns = _columns(header.partition(":")[2])
                        if columns is not None and columns != new_columns:
                            raise ValueError("conflicting column headers")
                        columns = new_columns
                except ValueError as exc:
                    raise _error(source, line_number, str(exc)) from exc
                continue
            if text.lower().startswith("tsn") and "hour" in text.lower():
                new_columns = _columns(text)
                if columns is not None and columns != new_columns:
                    raise _error(source, line_number, "conflicting column headers")
                columns = new_columns
                continue

            if columns is None:
                raise _error(source, line_number, "missing Columns header; cannot identify the TEC code combination")
            if len(set(columns)) != len(columns) or not _REQUIRED_COLUMNS.issubset(columns):
                raise _error(
                    source, line_number, "Columns header must include unique tsn, hour, el, az, tec.l1l2, validity"
                )
            code_names = _CODE_COLUMNS.intersection(columns)
            if len(code_names) != 1:
                raise _error(source, line_number, "expected exactly one tec.c1p2 or tec.p1p2 column")
            code_column = next(iter(code_names))
            data_rows += 1
            try:
                parts = text.split()
                if len(parts) != len(columns):
                    raise ValueError(f"expected {len(columns)} numeric columns, got {len(parts)}")
                values = dict(zip(columns, (_number(p) for p in parts), strict=True))
                tsn, hour, validity = values["tsn"], values["hour"], values["validity"]
                if tsn < 0 or not tsn.is_integer() or tsn > np.iinfo(np.int64).max:
                    raise ValueError("tsn must be a non-negative integer")
                if validity < 0 or not validity.is_integer() or validity > np.iinfo(np.int64).max:
                    raise ValueError("validity must be a non-negative integer bitmask")
                if not 0 <= hour < 24:
                    raise ValueError("hour must be in [0, 24)")
                if not -90 <= values["el"] <= 90 or not 0 <= values["az"] <= 360:
                    raise ValueError("elevation or azimuth is out of range")
                if hour <= last_hour or tsn <= last_tsn:
                    raise ValueError("repeated or non-increasing observation epoch")
                records.append(
                    [tsn, hour, values["el"], values["az"], values["tec.l1l2"], values[code_column], validity]
                )
                forced_breaks.append(force_break)
                force_break = False
                last_hour, last_tsn = hour, tsn
            except ValueError as exc:
                if invalid_rows == "error":
                    raise _error(source, line_number, str(exc)) from exc
                skipped += 1
                force_break = True
                if len(issues) < 10:
                    issues.append(str(_error(source, line_number, str(exc))))

    if not records or code_column is None:
        raise DatFormatError(f"{source}: no usable numeric observations")
    if station is None or satellite_id is None:
        raise DatFormatError(f"{source}: station and satellite identity require headers or a tec-suite filename")
    if day is not None and not 1 <= day <= 366:
        raise DatFormatError(f"{source}: day of year is out of range")
    data = np.asarray(records, dtype=np.float64)
    tsn_arr = data[:, 0].astype(np.int64)
    validity_arr = data[:, 6].astype(np.int64)
    phase_valid = (validity_arr & 0b11000) == 0  # Missing L1/L2 observations.
    code_mask = 0b1000100 if code_column == "tec.c1p2" else 0b1100000
    code_valid = (validity_arr & code_mask) == 0
    slip = (validity_arr & 0b11) != 0
    breaks = np.asarray(forced_breaks, dtype=np.bool_) | slip | ~phase_valid
    diffs = np.diff(data[:, 1]) * 3600
    cadence = max(interval or 0, sampling or 0) or (float(np.median(diffs)) if len(diffs) else 0)
    gaps = diffs > 1.5 * cadence if cadence else np.zeros(len(diffs), dtype=np.bool_)
    breaks[1:] |= gaps | ~phase_valid[:-1]
    arrays = [tsn_arr, *[data[:, i] for i in range(1, 6)], validity_arr, phase_valid, code_valid, breaks]
    for array in arrays:
        array.setflags(write=False)
    lon, lat, height = position if position is not None else (None, None, None)
    return SatelliteData(
        source,
        satellite_id,
        station,
        lon,
        lat,
        height,
        channel,
        interval,
        sampling,
        year,
        day,
        code_column,
        *arrays,
        DatDiagnostics(
            data_rows,
            len(records),
            skipped,
            int((~phase_valid).sum()),
            int((~code_valid).sum()),
            int(slip.sum()),
            int(gaps.sum()),
            tuple(issues),
        ),
    )


def read_tec_output(path: str | Path) -> NDArray[np.float64]:
    """Read eight numeric output columns. NaN coefficients retain unsolved epochs."""
    source = Path(path)
    rows = []
    with source.open(encoding="utf-8-sig") as stream:
        for number, raw in enumerate(stream, 1):
            text = raw.strip()
            if not text or text.startswith("#"):
                continue
            try:
                parts = text.split()
                if len(parts) != 8:
                    raise ValueError("expected eight output columns")
                row = [float(v.replace("D", "E").replace("d", "e")) for v in parts]
                if not math.isfinite(row[0]) or not 0 <= row[0] < 24 or any(math.isinf(v) for v in row[1:]):
                    raise ValueError("invalid output time or infinite coefficient")
                if rows and row[0] <= rows[-1][0]:
                    raise ValueError("output UT must increase strictly")
                rows.append(row)
            except ValueError as exc:
                raise _error(source, number, str(exc)) from exc
    if not rows:
        raise DatFormatError(f"{source}: no TEC output rows")
    return np.asarray(rows, dtype=np.float64)


def write_tec_output(path: str | Path, rows: NDArray[np.float64], *, overwrite: bool = False) -> Path:
    """Atomically publish eight-column TEC output, refusing overwrite by default.

    Unsolved rows use NaN coefficients so gaps remain distinguishable from
    physically valid zero TEC. The parent directory must already exist.
    """
    destination = Path(path)
    values = np.asarray(rows, dtype=np.float64)
    if values.ndim != 2 or values.shape[1] != 8 or len(values) == 0:
        raise ValueError("TEC output must be a nonempty (n, 8) array")
    if not np.all(np.isfinite(values[:, 0])) or np.any((values[:, 0] < 0) | (values[:, 0] >= 24)):
        raise ValueError("UT must be finite and in [0, 24)")
    if np.any(np.diff(values[:, 0]) <= 0) or np.any(np.isinf(values[:, 1:])):
        raise ValueError("UT must increase strictly and coefficients cannot be infinite")
    fd, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write("# " + "  ".join(TEC_COLUMNS) + "\n")
            np.savetxt(stream, values, fmt="%.12g")
            stream.flush()
            os.fsync(stream.fileno())
        if overwrite:
            os.replace(temporary, destination)
        else:
            # An exclusive hard link publishes a fully written file without
            # racing an existing-file check. Both files are on the same volume.
            os.link(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination
