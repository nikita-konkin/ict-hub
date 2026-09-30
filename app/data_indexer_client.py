"""Client helpers for reading indexed trees from the data-indexer FastAPI service."""

from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx

from app.config import DATA_INDEXER_CLIENT_CACHE_TTL_SEC, DATA_INDEXER_TIMEOUT_SEC, DATA_INDEXER_URL

logger = logging.getLogger(__name__)


# Short-lived in-process cache to avoid repeated HTTP calls while rendering pages.
# (endpoint, root) -> (expires_at monotonic seconds, value). The data-indexer
# keeps its own longer cache; this one only needs to absorb bursts.
_cache: dict[tuple[str, str], tuple[float, Any]] = {}


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


def _get_cached(cache_key: tuple[str, str]) -> list[dict[str, object]] | None:
    entry = _cache.get(cache_key)
    if entry is None:
        return None
    expires_at, value = entry
    if time.monotonic() >= expires_at:
        _cache.pop(cache_key, None)
        return None
    return value


def _set_cache(endpoint: str, root_path: str, value: list[dict[str, object]]) -> list[dict[str, object]]:
    _cache[(endpoint, root_path)] = (time.monotonic() + DATA_INDEXER_CLIENT_CACHE_TTL_SEC, value)
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


# Path characters left unescaped in the ?root= query value.
_ROOT_SAFE_CHARS = "/:\\"


async def _fetch_xml(endpoint: str, root_path: str) -> ET.Element | None:
    """GET an indexer endpoint and parse its XML; None if unconfigured or failing."""
    if not DATA_INDEXER_URL:
        return None

    base = DATA_INDEXER_URL.rstrip("/")
    url = f"{base}/{endpoint}"
    if root_path:
        url = f"{url}?root={quote(root_path, safe=_ROOT_SAFE_CHARS)}"

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
        return ET.fromstring(response.text)  # noqa: S314 - trusted internal service
    except Exception as exc:
        logger.warning("data-indexer request failed for %s: %s", endpoint, exc)
        return None


async def _cached_listing(
    endpoint: str,
    host_root: str,
    parse: Callable[[ET.Element], list[dict[str, object]]],
) -> list[dict[str, object]]:
    """Fetch and parse one indexer listing, reusing a recent result if there is one."""
    cache_key = (endpoint, host_root)
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    root = await _fetch_xml(endpoint, host_root)
    if root is None:
        # Failures are not cached, so the next page load retries.
        return []

    return _set_cache(endpoint, host_root, parse(root))


async def list_rinex_server_structure_async(host_root: str) -> list[dict[str, object]]:
    """RINEX tree from the indexer's /rinex endpoint."""
    return await _cached_listing("rinex", host_root, _parse_rinex_root)


async def list_tecsuite_output_structure_async(host_root: str) -> list[dict[str, object]]:
    """TEC-suite DAT output tree from the indexer's /tecsuite endpoint."""
    return await _cached_listing("tecsuite", host_root, _parse_tecsuite_root)


async def list_parquet_output_structure_async(host_root: str) -> list[dict[str, object]]:
    """Year/day tree of a parquet (or any YYYY/DDD) root from /parquet."""
    return await _cached_listing("parquet", host_root, _parse_parquet_root)


async def list_parquet_satellite_structure_async(host_root: str) -> list[dict[str, object]]:
    """Year/day/station/satellite tree from /parquet-satellites."""
    return await _cached_listing("parquet-satellites", host_root, _parse_parquet_sat_root)
