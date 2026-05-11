from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app import config as cfg
from app.auth import require_page_access
from app.data_indexer_client import get_rinex_station_map_async, list_rinex_server_structure_async
from app.i18n import apply_lang_cookie, template_context
from app.models import User
from app.registry import CONVERTERS

router = APIRouter(tags=["stations-map"])
templates = Jinja2Templates(directory="app/templates")


def _scan_root(*paths: str) -> str:
    for path in paths:
        value = (path or "").strip()
        if value:
            return value
    return ""


@router.get("/stations-map", response_class=HTMLResponse)
async def stations_map_page(
    request: Request,
    current_user: User = Depends(require_page_access("stations_map")),
    refresh: bool = Query(default=False),
):
    data_indexer_enabled = bool(cfg.DATA_INDEXER_URL.strip())
    rinex_root = _scan_root(cfg.RINEX_DATA_PATH_CONTAINER, cfg.RINEX_DATA_PATH_HOST)

    rinex_tree = (
        await list_rinex_server_structure_async(rinex_root, refresh=refresh)
        if data_indexer_enabled and rinex_root
        else []
    )

    response = templates.TemplateResponse(
        "stations_map.html",
        template_context(
            request,
            current_user=current_user,
            converters=CONVERTERS,
            data_indexer_enabled=data_indexer_enabled,
            rinex_root=rinex_root,
            rinex_tree=rinex_tree,
            refresh=refresh,
        ),
    )
    return apply_lang_cookie(request, response)


@router.get("/stations-map/data")
async def stations_map_data(
    current_user: User = Depends(require_page_access("stations_map")),
    year: str = Query(..., min_length=1),
    day: str = Query(default=""),
    refresh: bool = Query(default=False),
):
    _ = current_user

    if not cfg.DATA_INDEXER_URL.strip():
        raise HTTPException(status_code=503, detail="DATA_INDEXER_URL is not configured")

    rinex_root = _scan_root(cfg.RINEX_DATA_PATH_CONTAINER, cfg.RINEX_DATA_PATH_HOST)
    if not rinex_root:
        raise HTTPException(status_code=503, detail="RINEX data root is not configured")

    payload = await get_rinex_station_map_async(rinex_root, year=year, day=day, refresh=refresh)
    return JSONResponse(content=payload)
