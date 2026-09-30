"""Client helpers for reading indexed trees from the data-indexer FastAPI service."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote
import xml.etree.ElementTree as ET

import httpx

from app import config as cfg
from app.config import DATA_INDEXER_TIMEOUT_SEC, DATA_INDEXER_URL

logger = logging.getLogger(__name__)


# Short-lived in-process cache so rendering a page does not repeat HTTP calls.
# Entries expire (DATA_INDEXER_CLIENT_CACHE_TTL_SEC) so trees the indexer has
# refreshed, e.g. after a job wrote new output, reach the UI without a restart.
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


def _get_cache(endpoint: str, root_path: str) -> Any | None:
    entry = _cache.get((endpoint, root_path))
    if entry is None:
        return None
    stored_at, value = entry
    if time.monotonic() - stored_at > cfg.DATA_INDEXER_CLIENT_CACHE_TTL_SEC:
        _cache.pop((endpoint, root_path), None)
        return None
    return value


def _set_cache(endpoint: str, root_path: str, value: Any) -> Any:
    _cache[(endpoint, root_path)] = (time.monotonic(), value)
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


# Keep path separators (and Windows drive colons) readable in indexer URLs.
_ROOT_SAFE_CHARS = "/:\\"


def _indexer_url(
    endpoint: str,
    root_path: str,
    *,
    refresh: bool = False,
    extra_query: dict[str, str] | None = None,
) -> str | None:
    if not DATA_INDEXER_URL:
        return None

    query_parts: list[str] = []
    if root_path:
        query_parts.append(f"root={quote(root_path, safe=_ROOT_SAFE_CHARS)}")
    if refresh:
        query_parts.append("refresh=true")
    for key, value in (extra_query or {}).items():
        value_text = str(value or "").strip()
        if value_text:
            query_parts.append(f"{quote(str(key), safe='')}={quote(value_text, safe=_ROOT_SAFE_CHARS)}")

    url = f"{DATA_INDEXER_URL.rstrip('/')}/{endpoint}"
    return f"{url}?{'&'.join(query_parts)}" if query_parts else url


async def _fetch_async(
    endpoint: str,
    root_path: str,
    parse: Callable[[httpx.Response], Any],
    *,
    refresh: bool = False,
    extra_query: dict[str, str] | None = None,
) -> Any | None:
    """GET an indexer endpoint and parse the response; None when unavailable."""
    url = _indexer_url(endpoint, root_path, refresh=refresh, extra_query=extra_query)
    if url is None:
        return None

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
        return parse(response)
    except Exception as exc:  # noqa: BLE001 - external service errors should be non-fatal
        logger.warning("data-indexer request failed for %s: %s (%r)", endpoint, type(exc).__name__, exc)
        return None


async def _fetch_xml_async(endpoint: str, root_path: str, refresh: bool = False) -> ET.Element | None:
    return await _fetch_async(endpoint, root_path, lambda response: ET.fromstring(response.text), refresh=refresh)


def _json_object(response: httpx.Response) -> dict[str, Any] | None:
    payload = response.json()
    return payload if isinstance(payload, dict) else None


async def _fetch_json_async(
    endpoint: str,
    root_path: str,
    *,
    refresh: bool = False,
    extra_query: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    return await _fetch_async(endpoint, root_path, _json_object, refresh=refresh, extra_query=extra_query)


async def _cached_listing(
    endpoint: str,
    host_root: str,
    parse: Callable[[ET.Element], list[dict[str, object]]],
    refresh: bool,
) -> list[dict[str, object]]:
    cached = None if refresh else _get_cache(endpoint, host_root)
    if cached is not None:
        return cached

    root = await _fetch_xml_async(endpoint, host_root, refresh=refresh)
    if root is None:
        return []

    return _set_cache(endpoint, host_root, parse(root))


async def list_rinex_server_structure_async(host_root: str, refresh: bool = False) -> list[dict[str, object]]:
    """RINEX year/day tree from the indexer's /rinex endpoint."""
    return await _cached_listing("rinex", host_root, _parse_rinex_root, refresh)


async def list_tecsuite_output_structure_async(host_root: str, refresh: bool = False) -> list[dict[str, object]]:
    """TEC-suite DAT year/day/site tree from /tecsuite."""
    return await _cached_listing("tecsuite", host_root, _parse_tecsuite_root, refresh)


async def list_abstec_output_structure_async(host_root: str, refresh: bool = False) -> list[dict[str, object]]:
    """AbsTEC output tree from /abstec (same shape as TEC-suite)."""
    return await _cached_listing("abstec", host_root, _parse_tecsuite_root, refresh)


async def list_parquet_output_structure_async(host_root: str, refresh: bool = False) -> list[dict[str, object]]:
    """Parquet year/day tree from /parquet."""
    return await _cached_listing("parquet", host_root, _parse_parquet_root, refresh)


async def list_parquet_satellite_structure_async(host_root: str, refresh: bool = False) -> list[dict[str, object]]:
    """Parquet year/day/station/satellite tree from /parquet-satellites."""
    return await _cached_listing("parquet-satellites", host_root, _parse_parquet_sat_root, refresh)


def _empty_rinex_station_map_payload(year: str, day: str) -> dict[str, object]:
    return {
        "year": year,
        "day": day,
        "station_count": 0,
        "archive_count": 0,
        "stations": [],
    }


async def get_rinex_station_map_async(
    host_root: str,
    *,
    year: str,
    day: str = "",
    refresh: bool = False,
) -> dict[str, object]:
    cache_root = f"{host_root}|{year}|{day}"
    cache_key = ("rinex-stations", cache_root)
    cached = None if refresh else _get_cache(*cache_key)
    if cached is not None:
        return cached

    payload = await _fetch_json_async(
        "rinex-stations",
        host_root,
        refresh=refresh,
        extra_query={"year": year, "day": day},
    )
    if payload is None:
        return _empty_rinex_station_map_payload(year, day)

    return _set_cache("rinex-stations", cache_root, payload)
