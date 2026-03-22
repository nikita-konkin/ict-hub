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
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from app import config as cfg
from app.auth import get_current_user
from app.models import User
from app.registry import CONVERTERS

router = APIRouter(tags=["analysis"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/analysis", response_class=HTMLResponse)
async def analysis_home(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """Analysis landing page for TEC data exploration and exports."""
    return templates.TemplateResponse(
        "analysis.html",
        {
            "request": request,
            "current_user": current_user,
            "converters": CONVERTERS,
            "analysis_api_base_url": cfg.ANALYSIS_API_BASE_URL,
            "analysis_api_enabled": bool(cfg.ANALYSIS_API_BASE_URL.strip()),
        },
    )


@router.get("/analysis/api/{api_path:path}")
async def analysis_proxy(
    api_path: str,
    request: Request,
    current_user: User = Depends(get_current_user),
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
        async with httpx.AsyncClient(timeout=timeout) as client:
            upstream = await client.get(target_url)
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Analysis API timeout")
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Analysis API error: {exc}")

    passthrough_headers = {}
    for name in ("content-type", "content-disposition", "cache-control"):
        value = upstream.headers.get(name)
        if value:
            passthrough_headers[name] = value

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=passthrough_headers,
    )
