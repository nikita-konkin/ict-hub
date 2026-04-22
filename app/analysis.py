"""
analysis.py — Data-analysis section and API proxy.

This router integrates an external TEC Analysis Backend into ConverterHub.
It provides:
  - HTML page with quick links and usage examples
  - Authenticated proxy endpoint so users can call analysis APIs from the
    same session without exposing backend internals directly in the UI
"""
from __future__ import annotations

from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates

from app import config as cfg
from app.auth import get_current_user, require_page_access
from app.data_indexer_client import clear_cache as clear_data_indexer_cache, list_parquet_satellite_structure_async
from app.i18n import apply_lang_cookie, template_context
from app.models import User
from app.registry import CONVERTERS

router = APIRouter(tags=["analysis"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/analysis", response_class=HTMLResponse)
async def analysis_home(
    request: Request,
    current_user: User = Depends(require_page_access("analysis")),
):
    """Analysis landing page for TEC data exploration and exports."""
    response = templates.TemplateResponse(
        "analysis.html",
        template_context(
            request,
            current_user=current_user,
            converters=CONVERTERS,
            analysis_api_base_url=cfg.ANALYSIS_API_BASE_URL,
            analysis_api_enabled=bool(cfg.ANALYSIS_API_BASE_URL.strip()),
        ),
    )
    return apply_lang_cookie(request, response)


@router.get("/analysis/index-options")
async def analysis_index_options(
    current_user: User = Depends(require_page_access("analysis")),
):
    """Return endpoint-aware options from parquet-only data-indexer APIs."""
    _ = current_user  # explicit auth guard via dependency

    clear_data_indexer_cache()

    def _sort_doy_key(name: str) -> tuple[int, int, str]:
        try:
            return (int(name), len(name), name)
        except ValueError:
            return (9999, len(name), name)

    def _build_source_payload(tree: list[dict[str, object]]) -> dict[str, object]:
        years: list[str] = []
        doys_by_year: dict[str, list[str]] = {}
        stations_by_year_doy: dict[str, dict[str, list[str]]] = {}
        satellites_by_year_doy: dict[str, dict[str, list[str]]] = {}

        for year_item in tree:
            year = str(year_item.get("year", "")).strip()
            if not year:
                continue
            years.append(year)

            doys: list[str] = []
            stations_for_year: dict[str, list[str]] = {}
            satellites_for_year: dict[str, list[str]] = {}

            days_raw = year_item.get("days", [])
            if isinstance(days_raw, list):
                for day_item in days_raw:
                    if not isinstance(day_item, dict):
                        continue
                    doy = str(day_item.get("day", "")).strip()
                    if not doy:
                        continue
                    doys.append(doy)

                    stations_raw = day_item.get("stations", [])
                    satellites_raw = day_item.get("satellites", [])
                    stations_for_year[doy] = (
                        sorted({str(v).strip() for v in stations_raw if str(v).strip()})
                        if isinstance(stations_raw, list)
                        else []
                    )
                    satellites_for_year[doy] = (
                        sorted({str(v).strip() for v in satellites_raw if str(v).strip()})
                        if isinstance(satellites_raw, list)
                        else []
                    )

            doys_by_year[year] = sorted(set(doys), key=_sort_doy_key)
            stations_by_year_doy[year] = stations_for_year
            satellites_by_year_doy[year] = satellites_for_year

        sorted_years = sorted(set(years), key=lambda y: int(y), reverse=True)
        return {
            "years": sorted_years,
            "doysByYear": doys_by_year,
            "stationsByYearDoy": stations_by_year_doy,
            "satellitesByYearDoy": satellites_by_year_doy,
        }

    abs_scan = cfg.PARQUET_OUTPUT_ABSTEC_DATA_PATH_CONTAINER.strip() or cfg.PARQUET_OUTPUT_ABSTEC_DATA_PATH_HOST.strip()
    abs_tree = await list_parquet_satellite_structure_async(abs_scan) if abs_scan else []

    tec_scan = cfg.PARQUET_OUTPUT_TECSUITE_DATA_PATH_CONTAINER.strip() or cfg.PARQUET_OUTPUT_TECSUITE_DATA_PATH_HOST.strip()
    tec_tree = await list_parquet_satellite_structure_async(tec_scan) if tec_scan else []

    return JSONResponse(content={"absoltec": _build_source_payload(abs_tree), "tec": _build_source_payload(tec_tree)})


def _filter_outgoing_headers(request: Request) -> dict[str, str]:
    hop_by_hop = {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "host",
    }
    return {
        name: value
        for name, value in request.headers.items()
        if name.lower() not in hop_by_hop
    }


def _filter_incoming_headers(headers: dict[str, str]) -> dict[str, str]:
    hop_by_hop = {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "host",
    }
    return {
        name: value
        for name, value in headers.items()
        if name.lower() not in hop_by_hop
    }


@router.api_route("/analysis/api/{api_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def analysis_proxy(
    api_path: str,
    request: Request,
    current_user: User = Depends(require_page_access("analysis")),
    tail: int | None = Query(default=None, ge=0),
):
    """
    Proxy to external TEC Analysis Backend.

    Example:
      /analysis/api/absoltec/raw?year=2026&doy=1&station=aksu&format=csv
    """
    _ = current_user  # explicit auth guard via dependency

    base_url = cfg.ANALYSIS_API_BASE_URL.strip().rstrip("/")
    if not base_url:
        raise HTTPException(
            status_code=503,
            detail="ANALYSIS_API_BASE_URL is not configured",
        )

    safe_path = api_path.lstrip("/")
    if not safe_path:
        raise HTTPException(status_code=400, detail="API path is required")

    query_items = list(request.query_params.multi_items())
    # Keep compatibility with endpoints that may use a `tail` query later.
    if tail is not None and not any(k == "tail" for k, _ in query_items):
        query_items.append(("tail", str(tail)))

    query_string = urlencode(query_items, doseq=True)
    target_url = f"{base_url}/{safe_path}"
    if query_string:
        target_url = f"{target_url}?{query_string}"

    timeout = httpx.Timeout(cfg.ANALYSIS_API_TIMEOUT_SEC)
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            upstream = await client.request(
                method=request.method,
                url=target_url,
                headers=_filter_outgoing_headers(request),
                content=await request.body(),
            )
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Analysis API timeout")
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Analysis API error (base={base_url}): {exc}",
        )

    response_headers = _filter_incoming_headers(dict(upstream.headers))
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=response_headers,
        media_type=upstream.headers.get("content-type"),
    )
