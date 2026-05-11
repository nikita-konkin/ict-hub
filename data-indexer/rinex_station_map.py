from __future__ import annotations

import logging
import math
import os
import re
import time
import zipfile
from pathlib import Path
from statistics import median
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)

_CACHE_TTL_SEC = float(os.getenv("DATA_INDEXER_CACHE_TTL_SEC", "300.0"))
_MAX_HEADER_LINES = 512
_OBSERVATION_MEMBER_PATTERN = re.compile(
    r"\.(?:\d{2}[od]|obs|rnx|crx)(?:\.(?:gz|zip))?$",
    re.IGNORECASE,
)
_STATION_ID_PATTERN = re.compile(r"([a-z0-9]{4})\d{3}", re.IGNORECASE)
_NON_ALNUM_PATTERN = re.compile(r"[^a-z0-9]+")
_YEAR_DIR_RE = re.compile(r"^\d{4}_original$")
_DAY_DIR_RE = re.compile(r"^\d{2,3}$")
_DAY_IN_MONTH_RE = re.compile(r"^\d{2}$")

_WGS84_A_M = 6378137.0
_WGS84_F = 1.0 / 298.257223563
_WGS84_B_M = _WGS84_A_M * (1.0 - _WGS84_F)
_WGS84_E2 = _WGS84_F * (2.0 - _WGS84_F)
_WGS84_EP2 = _WGS84_E2 / (1.0 - _WGS84_E2)

_cache_lock = Lock()
_station_map_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def list_rinex_station_map(
    host_root: str,
    *,
    year: str,
    day: str = "",
    refresh: bool = False,
) -> dict[str, Any]:
    if not host_root:
        raise ValueError("RINEX root is not configured.")

    root = Path(host_root)
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"RINEX root does not exist: {host_root}")

    year_root = _resolve_year_root(root, year)
    scan_root, normalized_day = _resolve_scan_root(year_root, day)
    cache_key = _build_cache_key(host_root, year_root.name, normalized_day)

    now = time.monotonic()
    with _cache_lock:
        cached = _station_map_cache.get(cache_key)
        if cached is not None and not refresh and now - cached[0] < _CACHE_TTL_SEC:
            logger.debug("[RINEX-STATIONS] Cache HIT for %s", cache_key)
            return cached[1]

    logger.info(
        "[RINEX-STATIONS] Scanning root=%s year=%s day=%s",
        host_root,
        year_root.name,
        normalized_day or "<all>",
    )
    payload = _scan_station_map(root=root, year_root=year_root, scan_root=scan_root, day=normalized_day)

    with _cache_lock:
        _station_map_cache[cache_key] = (time.monotonic(), payload)
    return payload


def _build_cache_key(host_root: str, year: str, day: str) -> str:
    return f"{host_root}|{year}|{day}"


def _resolve_year_root(root: Path, year: str) -> Path:
    year_name = str(year or "").strip()
    if not _YEAR_DIR_RE.fullmatch(year_name):
        raise ValueError(f"Invalid RINEX year folder: {year_name}")

    year_root = root / year_name
    if not year_root.exists() or not year_root.is_dir():
        raise FileNotFoundError(f"RINEX year folder does not exist: {year_name}")
    return year_root


def _resolve_scan_root(year_root: Path, day: str) -> tuple[Path, str]:
    raw_day = str(day or "").strip().replace("\\", "/")
    if not raw_day:
        return year_root, ""

    if "/" in raw_day:
        parts = [part for part in raw_day.split("/") if part]
        if len(parts) != 2 or not all(_DAY_IN_MONTH_RE.fullmatch(part) for part in parts):
            raise ValueError(f"Invalid RINEX month/day folder: {raw_day}")
        scan_root = year_root / parts[0] / parts[1]
        normalized_day = f"{parts[0]}/{parts[1]}"
    else:
        if not _DAY_DIR_RE.fullmatch(raw_day):
            raise ValueError(f"Invalid RINEX day folder: {raw_day}")
        scan_root = year_root / raw_day
        normalized_day = raw_day

    if not scan_root.exists() or not scan_root.is_dir():
        raise FileNotFoundError(f"RINEX day folder does not exist: {normalized_day}")
    return scan_root, normalized_day


def _scan_station_map(
    *,
    root: Path,
    year_root: Path,
    scan_root: Path,
    day: str,
) -> dict[str, Any]:
    archive_paths = sorted(path for path in scan_root.rglob("*.zip") if path.is_file())
    records: list[dict[str, Any]] = []

    for archive_path in archive_paths:
        record = _extract_station_header_record(archive_path, root=root, year_root=year_root)
        if record is not None:
            records.append(record)

    stations = _summarize_station_records(records)
    latitudes = [float(item["latitude_deg"]) for item in stations]
    longitudes = [float(item["longitude_deg"]) for item in stations]

    payload: dict[str, Any] = {
        "root": str(root),
        "year": year_root.name,
        "day": day,
        "scan_path": str(scan_root),
        "archive_count": len(records),
        "station_count": len(stations),
        "stations": stations,
        "generated_at": time.time(),
    }

    if latitudes and longitudes:
        payload["map_center"] = {
            "latitude_deg": (min(latitudes) + max(latitudes)) / 2.0,
            "longitude_deg": (min(longitudes) + max(longitudes)) / 2.0,
        }
        payload["map_bounds"] = {
            "latitude_min": min(latitudes),
            "latitude_max": max(latitudes),
            "longitude_min": min(longitudes),
            "longitude_max": max(longitudes),
        }

    return payload


def _extract_station_header_record(
    archive_path: Path,
    *,
    root: Path,
    year_root: Path,
) -> dict[str, Any] | None:
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.infolist():
                if member.is_dir():
                    continue
                if not _OBSERVATION_MEMBER_PATTERN.search(member.filename):
                    continue

                header_lines = _read_rinex_header_lines(archive, member.filename)
                if not _is_observation_header(header_lines):
                    continue

                metadata = _parse_rinex_header_metadata(header_lines)
                if metadata is None:
                    continue

                x_m = metadata["x_m"]
                y_m = metadata["y_m"]
                z_m = metadata["z_m"]
                if max(abs(x_m), abs(y_m), abs(z_m)) < 1.0:
                    continue

                latitude_deg, longitude_deg, altitude_m = _ecef_to_geodetic(x_m, y_m, z_m)
                station_id = (
                    _extract_station_id(member.filename)
                    or _extract_station_id(archive_path.stem)
                    or _extract_station_id(str(metadata.get("marker_name") or ""))
                )
                if not station_id:
                    continue

                rel_to_root = archive_path.relative_to(root).as_posix()
                rel_to_year = archive_path.relative_to(year_root)
                archive_day = _extract_archive_day(rel_to_year)
                return {
                    "archive_path": rel_to_root,
                    "archive_name": archive_path.name,
                    "archive_day": archive_day,
                    "obs_member_name": member.filename,
                    "station_id": station_id.upper(),
                    "marker_name": metadata.get("marker_name") or "",
                    "marker_number": metadata.get("marker_number") or "",
                    "x_m": float(x_m),
                    "y_m": float(y_m),
                    "z_m": float(z_m),
                    "latitude_deg": float(latitude_deg),
                    "longitude_deg": float(longitude_deg),
                    "altitude_m": float(altitude_m),
                }
    except (OSError, zipfile.BadZipFile):
        logger.debug("[RINEX-STATIONS] Skipping unreadable archive: %s", archive_path)
        return None

    return None


def _extract_archive_day(relative_to_year: Path) -> str:
    parts = list(relative_to_year.parts)
    if len(parts) >= 3 and _DAY_IN_MONTH_RE.fullmatch(parts[0]) and _DAY_IN_MONTH_RE.fullmatch(parts[1]):
        return f"{parts[0]}/{parts[1]}"
    if len(parts) >= 2 and _DAY_DIR_RE.fullmatch(parts[0]):
        return parts[0]
    return ""


def _read_rinex_header_lines(archive: zipfile.ZipFile, member_name: str) -> list[str]:
    header_lines: list[str] = []
    with archive.open(member_name, "r") as handle:
        for line_index, raw_line in enumerate(handle):
            line = raw_line.decode("ascii", errors="ignore").rstrip("\r\n")
            header_lines.append(line)
            if _header_label(line) == "END OF HEADER":
                break
            if line_index + 1 >= _MAX_HEADER_LINES:
                break
    return header_lines


def _is_observation_header(header_lines: list[str]) -> bool:
    if not header_lines:
        return False
    version_line = " ".join(header_lines[:2]).upper()
    return "RINEX VERSION / TYPE" in version_line and "OBSERVATION DATA" in version_line


def _parse_rinex_header_metadata(header_lines: list[str]) -> dict[str, Any] | None:
    marker_name = ""
    marker_number = ""
    xyz_values: tuple[float, float, float] | None = None

    for line in header_lines:
        label = _header_label(line)
        value = _header_value(line)
        if label == "MARKER NAME":
            marker_name = value
        elif label == "MARKER NUMBER":
            marker_number = value
        elif label == "APPROX POSITION XYZ":
            parts = value.split()
            if len(parts) >= 3:
                try:
                    xyz_values = (float(parts[0]), float(parts[1]), float(parts[2]))
                except ValueError:
                    xyz_values = None

    if xyz_values is None:
        return None

    return {
        "marker_name": marker_name,
        "marker_number": marker_number,
        "x_m": xyz_values[0],
        "y_m": xyz_values[1],
        "z_m": xyz_values[2],
    }


def _header_label(line: str) -> str:
    return line[60:].strip() if len(line) > 60 else ""


def _header_value(line: str) -> str:
    return line[:60].strip()


def _extract_station_id(text: str) -> str:
    match = _STATION_ID_PATTERN.search(text)
    if match:
        return match.group(1)
    normalized = _NON_ALNUM_PATTERN.sub("", str(text or "").lower())
    return normalized[:4] if len(normalized) >= 4 else ""


def _summarize_station_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(str(record["station_id"]), []).append(record)

    stations: list[dict[str, Any]] = []
    for station_id in sorted(grouped):
        group = grouped[station_id]
        x_values = [float(item["x_m"]) for item in group]
        y_values = [float(item["y_m"]) for item in group]
        z_values = [float(item["z_m"]) for item in group]
        median_x = float(median(x_values))
        median_y = float(median(y_values))
        median_z = float(median(z_values))
        latitude_deg, longitude_deg, altitude_m = _ecef_to_geodetic(median_x, median_y, median_z)
        coordinate_spread_m = max(
            (
                math.dist((median_x, median_y, median_z), (item["x_m"], item["y_m"], item["z_m"]))
                for item in group
            ),
            default=0.0,
        )

        days = sorted(
            {str(item["archive_day"]) for item in group if str(item["archive_day"]).strip()},
            key=_day_sort_key,
        )
        stations.append(
            {
                "station_id": station_id,
                "marker_name": _most_common_non_empty(item["marker_name"] for item in group),
                "marker_number": _most_common_non_empty(item["marker_number"] for item in group),
                "archive_count": len(group),
                "days": days,
                "first_archive": group[0]["archive_path"],
                "last_archive": group[-1]["archive_path"],
                "sample_member": group[0]["obs_member_name"],
                "x_m": median_x,
                "y_m": median_y,
                "z_m": median_z,
                "latitude_deg": float(latitude_deg),
                "longitude_deg": float(longitude_deg),
                "altitude_m": float(altitude_m),
                "coordinate_spread_m": float(coordinate_spread_m),
            }
        )

    return stations


def _most_common_non_empty(values: Any) -> str:
    counts: dict[str, int] = {}
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        counts[text] = counts.get(text, 0) + 1
    if not counts:
        return ""
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _day_sort_key(name: str) -> tuple[int, int, str]:
    if "/" in name:
        month, day = name.split("/", 1)
        return (int(month), int(day), name)
    return (int(name), len(name), name)


def _ecef_to_geodetic(x_m: float, y_m: float, z_m: float) -> tuple[float, float, float]:
    p = math.hypot(x_m, y_m)
    lon = 0.0 if p < 1e-9 else math.atan2(y_m, x_m)
    theta = math.atan2(z_m * _WGS84_A_M, p * _WGS84_B_M)
    sin_theta = math.sin(theta)
    cos_theta = math.cos(theta)
    lat = math.atan2(
        z_m + _WGS84_EP2 * _WGS84_B_M * sin_theta**3,
        p - _WGS84_E2 * _WGS84_A_M * cos_theta**3,
    )
    sin_lat = math.sin(lat)
    radius = _WGS84_A_M / math.sqrt(1.0 - _WGS84_E2 * sin_lat**2)
    alt = p / max(math.cos(lat), 1e-12) - radius
    return math.degrees(lat), math.degrees(lon), alt
