"""Client helpers for reading indexed trees from the data-indexer FastAPI service."""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote
import xml.etree.ElementTree as ET
from zipfile import Path

import httpx

from app.config import DATA_INDEXER_TIMEOUT_SEC, DATA_INDEXER_URL

logger = logging.getLogger(__name__)


# Keep short-lived in-process cache to avoid repeated HTTP calls while rendering pages.
_cache: dict[tuple[str, str], Any] = {}


def clear_cache() -> None:
    """Clear in-process data-indexer response cache."""
    _cache.clear()


def _text(node: ET.Element | None, default: str = "") -> str:
    if node is None or node.text is None:
        return default
    return node.text.strip()


def _as_int(value: str, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _fetch_xml(endpoint: str, root_path: str) -> ET.Element | None:
    if not DATA_INDEXER_URL:
        return None

    base = DATA_INDEXER_URL.rstrip("/")
    url = f"{base}/{endpoint}"
    if root_path:
        url = f"{url}?root={quote(root_path, safe='/:\\')}"

    try:
        # trust_env=False avoids accidental proxy routing for internal Docker service calls.
        response = httpx.get(url, timeout=DATA_INDEXER_TIMEOUT_SEC, trust_env=False)
        if response.status_code >= 400:
            logger.warning(
                "data-indexer upstream status=%s endpoint=%s url=%s server=%s body_prefix=%s",
                response.status_code,
                endpoint,
                url,
                response.headers.get("server", ""),
                response.text[:180],
            )
        response.raise_for_status()
        root = ET.fromstring(response.text)
        return root
    except Exception as exc:  # noqa: BLE001 - external service errors should be non-fatal
        logger.warning("data-indexer request failed for %s: %s", endpoint, exc)
        return None


def _set_cache(endpoint: str, root_path: str, value: list[dict[str, object]]) -> list[dict[str, object]]:
    _cache[(endpoint, root_path)] = value
    return value


def _parse_rinex_root(root: ET.Element) -> list[dict[str, object]]:
    years: list[dict[str, object]] = []
    for item in root.findall("item"):
        year_name = _text(item.find("year"))
        if not year_name:
            continue

        days: list[dict[str, object]] = []
        days_node = item.find("days")
        if days_node is not None:
            for day_item in days_node.findall("item"):
                day_name = _text(day_item.find("day"))
                stations = _as_int(_text(day_item.find("stations"), "0"), 0)
                if day_name:
                    days.append({"day": day_name, "stations": stations})

        years.append({"year": year_name, "days": days})

    return years


def _parse_tecsuite_root(root: ET.Element) -> list[dict[str, object]]:
    years: list[dict[str, object]] = []
    for item in root.findall("item"):
        year_name = _text(item.find("year"))
        if not year_name:
            continue

        days: list[dict[str, object]] = []
        days_node = item.find("days")
        if days_node is not None:
            for day_item in days_node.findall("item"):
                day_name = _text(day_item.find("day"))
                sites: list[str] = []
                sites_node = day_item.find("sites")
                if sites_node is not None:
                    for site_item in sites_node.findall("item"):
                        site_name = _text(site_item)
                        if site_name:
                            sites.append(site_name)
                if day_name:
                    days.append({"day": day_name, "sites": sites})

        years.append({"year": year_name, "days": days})

    return years


def _parse_parquet_root(root: ET.Element) -> list[dict[str, object]]:
    years: list[dict[str, object]] = []
    for item in root.findall("item"):
        year_name = _text(item.find("year"))
        if not year_name:
            continue

        days: list[str] = []
        days_node = item.find("days")
        if days_node is not None:
            for day_item in days_node.findall("item"):
                day_name = _text(day_item)
                if day_name:
                    days.append(day_name)

        years.append({"year": year_name, "days": days})

    return years


def _parse_parquet_sat_root(root: ET.Element) -> list[dict[str, object]]:
    years: list[dict[str, object]] = []
    for item in root.findall("item"):
        year_name = _text(item.find("year"))
        if not year_name:
            continue

        days: list[dict[str, object]] = []
        days_node = item.find("days")
        if days_node is not None:
            for day_item in days_node.findall("item"):
                day_name = _text(day_item.find("day"))
                if not day_name:
                    continue

                stations: list[str] = []
                satellites: list[str] = []

                stations_node = day_item.find("stations")
                if stations_node is not None:
                    for station_item in stations_node.findall("item"):
                        station_name = _text(station_item)
                        if station_name:
                            stations.append(station_name)

                satellites_node = day_item.find("satellites")
                if satellites_node is not None:
                    for sat_item in satellites_node.findall("item"):
                        sat_name = _text(sat_item)
                        if sat_name:
                            satellites.append(sat_name)

                days.append({"day": day_name, "stations": stations, "satellites": satellites})

        years.append({"year": year_name, "days": days})

    return years


async def _fetch_xml_async(endpoint: str, root_path: str) -> "ET.Element | None":
    """Async version of _fetch_xml — does not block the event loop."""
    if not DATA_INDEXER_URL:
        return None

    base = DATA_INDEXER_URL.rstrip("/")
    url = f"{base}/{endpoint}"
    if root_path:
        url = f"{url}?root={quote(root_path, safe='/:\\')}"

    try:
        # trust_env=False avoids accidental proxy routing for internal Docker service calls.
        async with httpx.AsyncClient(timeout=DATA_INDEXER_TIMEOUT_SEC, trust_env=False) as client:
            response = await client.get(url)
        if response.status_code >= 400:
            logger.warning(
                "data-indexer upstream status=%s endpoint=%s url=%s server=%s body_prefix=%s",
                response.status_code,
                endpoint,
                url,
                response.headers.get("server", ""),
                response.text[:180],
            )
        response.raise_for_status()
        return ET.fromstring(response.text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("data-indexer request failed for %s: %s", endpoint, exc)
        return None


async def list_parquet_satellite_structure_async(host_root: str) -> list[dict[str, object]]:
    """Async variant of list_parquet_satellite_structure — for use inside async FastAPI handlers."""
    cache_key = ("parquet-satellites", host_root)
    if cache_key in _cache:
        return _cache[cache_key]  # type: ignore[return-value]

    root = await _fetch_xml_async("parquet-satellites", host_root)
    if root is None:
        return []

    return _set_cache("parquet-satellites", host_root, _parse_parquet_sat_root(root))


async def list_rinex_server_structure_async(host_root: str) -> list[dict[str, object]]:
    cache_key = ("rinex", host_root)
    if cache_key in _cache:
        return _cache[cache_key]  # type: ignore[return-value]

    root = await _fetch_xml_async("rinex", host_root)
    if root is None:
        return []

    return _set_cache("rinex", host_root, _parse_rinex_root(root))


async def list_tecsuite_output_structure_async(host_root: str) -> list[dict[str, object]]:
    cache_key = ("tecsuite", host_root)
    if cache_key in _cache:
        return _cache[cache_key]  # type: ignore[return-value]

    root = await _fetch_xml_async("tecsuite", host_root)
    if root is None:
        return []

    return _set_cache("tecsuite", host_root, _parse_tecsuite_root(root))


async def list_parquet_output_structure_async(host_root: str) -> list[dict[str, object]]:
    cache_key = ("parquet", host_root)
    if cache_key in _cache:
        return _cache[cache_key]  # type: ignore[return-value]

    root = await _fetch_xml_async("parquet", host_root)
    if root is None:
        return []

    return _set_cache("parquet", host_root, _parse_parquet_root(root))


def list_rinex_server_structure(host_root: str) -> list[dict[str, object]]:
    """Return RINEX tree from data-indexer /rinex endpoint."""
    cache_key = ("rinex", host_root)
    if cache_key in _cache:
        return _cache[cache_key]

    root = _fetch_xml("rinex", host_root)
    if root is None:
        return []

    return _set_cache("rinex", host_root, _parse_rinex_root(root))


def list_tecsuite_output_structure(host_root: str) -> list[dict[str, object]]:
    """Return DAT tree from data-indexer /tecsuite endpoint."""
    cache_key = ("tecsuite", host_root)
    if cache_key in _cache:
        return _cache[cache_key]

    root = _fetch_xml("tecsuite", host_root)
    if root is None:
        return []

    return _set_cache("tecsuite", host_root, _parse_tecsuite_root(root))


def list_parquet_output_structure(host_root: str) -> list[dict[str, object]]:
    """Return parquet tree from data-indexer /parquet endpoint."""
    cache_key = ("parquet", host_root)
    if cache_key in _cache:
        return _cache[cache_key]

    root = _fetch_xml("parquet", host_root)
    if root is None:
        return []

    return _set_cache("parquet", host_root, _parse_parquet_root(root))


def list_parquet_satellite_structure(host_root: str) -> list[dict[str, object]]:
    """Return parquet year/day/station/satellite tree from /parquet-satellites."""
    cache_key = ("parquet-satellites", host_root)
    if cache_key in _cache:
        return _cache[cache_key]

    root = _fetch_xml("parquet-satellites", host_root)
    if root is None:
        return []

    return _set_cache("parquet-satellites", host_root, _parse_parquet_sat_root(root))
