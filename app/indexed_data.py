"""
indexed_data.py — Indexed data browser page.

Shows the folder trees currently indexed by the data-indexer service for:
  - RINEX
  - TEC-suite DAT output
  - AbsTEC output
  - Parquet outputs (TEC-suite / AbsTEC)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app import config as cfg
from app.auth import get_current_user
from app.data_indexer_client import (
    clear_cache as clear_data_indexer_cache,
    list_abstec_output_structure_async,
    list_parquet_output_structure_async,
    list_rinex_server_structure_async,
    list_tecsuite_output_structure_async,
)
from app.i18n import apply_lang_cookie, template_context
from app.models import User
from app.registry import CONVERTERS

router = APIRouter(tags=["indexed-data"])
templates = Jinja2Templates(directory="app/templates")


def _scan_root(*paths: str) -> str:
    for path in paths:
        value = (path or "").strip()
        if value:
            return value
    return ""


@router.get("/indexed-data", response_class=HTMLResponse)
async def indexed_data_page(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    clear_data_indexer_cache()

    data_indexer_enabled = bool(cfg.DATA_INDEXER_URL.strip())

    roots = {
        "rinex": _scan_root(cfg.RINEX_DATA_PATH_CONTAINER, cfg.RINEX_DATA_PATH_HOST),
        "tecsuite": _scan_root(cfg.TECSUITE_OUT_DAT_DATA_PATH_CONTAINER, cfg.TECSUITE_OUT_DAT_DATA_PATH_HOST),
        "abstec": _scan_root(cfg.ABSTEC_OUTPUT_DATA_PATH_CONTAINER, cfg.ABSTEC_OUTPUT_DATA_PATH_HOST),
        "parquet_tecsuite": _scan_root(
            cfg.PARQUET_OUTPUT_TECSUITE_DATA_PATH_CONTAINER, cfg.PARQUET_OUTPUT_TECSUITE_DATA_PATH_HOST
        ),
        "parquet_abstec": _scan_root(cfg.PARQUET_OUTPUT_ABSTEC_DATA_PATH_CONTAINER, cfg.PARQUET_OUTPUT_ABSTEC_DATA_PATH_HOST),
    }

    rinex_tree = await list_rinex_server_structure_async(roots["rinex"]) if data_indexer_enabled and roots["rinex"] else []
    tecsuite_tree = (
        await list_tecsuite_output_structure_async(roots["tecsuite"]) if data_indexer_enabled and roots["tecsuite"] else []
    )
    abstec_tree = await list_abstec_output_structure_async(roots["abstec"]) if data_indexer_enabled and roots["abstec"] else []

    parquet_tecsuite_tree = (
        await list_parquet_output_structure_async(roots["parquet_tecsuite"])
        if data_indexer_enabled and roots["parquet_tecsuite"]
        else []
    )
    parquet_abstec_tree = (
        await list_parquet_output_structure_async(roots["parquet_abstec"]) if data_indexer_enabled and roots["parquet_abstec"] else []
    )

    response = templates.TemplateResponse(
        "indexed_data.html",
        template_context(
            request,
            current_user=current_user,
            converters=CONVERTERS,
            data_indexer_enabled=data_indexer_enabled,
            roots=roots,
            rinex_tree=rinex_tree,
            tecsuite_tree=tecsuite_tree,
            abstec_tree=abstec_tree,
            parquet_tecsuite_tree=parquet_tecsuite_tree,
            parquet_abstec_tree=parquet_abstec_tree,
        ),
    )
    return apply_lang_cookie(request, response)

