"""
indexed_data.py — Indexed data browser page.

Shows the folder trees currently indexed by the data-indexer service for:
  - RINEX
  - TEC-suite DAT output
  - AbsTEC output
  - Parquet outputs (TEC-suite / AbsTEC)
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app import config as cfg
from app.auth import get_current_user, require_page_access
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


# Indexed day folders arrive in two shapes: DOY ("15", "015") for TEC-suite /
# AbsTEC / Parquet, and MM/DD ("01/15") for the RINEX layout B trees. The page
# shows day-of-year everywhere, so both shapes are normalized before rendering.
_DOY_RE = re.compile(r"^\d{1,3}$")
_MONTH_DAY_RE = re.compile(r"^(\d{1,2})/(\d{1,2})$")
_YEAR_PREFIX_RE = re.compile(r"^(\d{4})")


def _year_number(year_label: object) -> int | None:
    match = _YEAR_PREFIX_RE.match(str(year_label or "").strip())
    return int(match.group(1)) if match else None


def _day_labels(day_label: object, year_label: object) -> tuple[str, str]:
    """Return (doy, calendar_date) display labels for one indexed day folder.

    The calendar date is empty when it cannot be derived (unknown year or an
    unrecognized folder name); the DOY falls back to the raw folder name.
    """
    raw = str(day_label or "").strip()
    year = _year_number(year_label)

    if _DOY_RE.fullmatch(raw):
        doy = int(raw)
        if year and 1 <= doy <= 366:
            try:
                calendar = date(year, 1, 1) + timedelta(days=doy - 1)
            except (ValueError, OverflowError):
                return f"{doy:03d}", ""
            if calendar.year != year:
                return f"{doy:03d}", ""
            return f"{doy:03d}", calendar.isoformat()
        return f"{doy:03d}", ""

    match = _MONTH_DAY_RE.fullmatch(raw)
    if match and year:
        try:
            calendar = date(year, int(match.group(1)), int(match.group(2)))
        except ValueError:
            return raw, ""
        return f"{calendar.timetuple().tm_yday:03d}", calendar.isoformat()

    return raw, ""


def _with_doy_days(tree: list[dict[str, object]]) -> list[dict[str, object]]:
    """Copy a year/day tree with every day relabeled as day-of-year.

    Handles both day shapes returned by the indexer: dicts (RINEX, TEC-suite,
    AbsTEC) and bare strings (Parquet).
    """
    normalized: list[dict[str, object]] = []
    for year in tree:
        year_label = year.get("year", "")
        days: list[object] = []
        for day in year.get("days") or []:
            if isinstance(day, dict):
                doy, calendar = _day_labels(day.get("day"), year_label)
                days.append({**day, "day": doy, "date": calendar})
            else:
                doy, _ = _day_labels(day, year_label)
                days.append(doy)
        normalized.append({**year, "days": days})
    return normalized


@router.get("/indexed-data", response_class=HTMLResponse)
async def indexed_data_page(
    request: Request,
    current_user: User = Depends(require_page_access("indexed_data")),
    refresh: bool = Query(default=False),
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

    rinex_tree = (
        await list_rinex_server_structure_async(roots["rinex"], refresh=refresh)
        if data_indexer_enabled and roots["rinex"]
        else []
    )
    tecsuite_tree = (
        await list_tecsuite_output_structure_async(roots["tecsuite"], refresh=refresh)
        if data_indexer_enabled and roots["tecsuite"]
        else []
    )
    abstec_tree = (
        await list_abstec_output_structure_async(roots["abstec"], refresh=refresh)
        if data_indexer_enabled and roots["abstec"]
        else []
    )

    parquet_tecsuite_tree = (
        await list_parquet_output_structure_async(roots["parquet_tecsuite"], refresh=refresh)
        if data_indexer_enabled and roots["parquet_tecsuite"]
        else []
    )
    parquet_abstec_tree = (
        await list_parquet_output_structure_async(roots["parquet_abstec"], refresh=refresh)
        if data_indexer_enabled and roots["parquet_abstec"]
        else []
    )

    response = templates.TemplateResponse(
        "indexed_data.html",
        template_context(
            request,
            current_user=current_user,
            converters=CONVERTERS,
            data_indexer_enabled=data_indexer_enabled,
            roots=roots,
            rinex_tree=_with_doy_days(rinex_tree),
            tecsuite_tree=_with_doy_days(tecsuite_tree),
            abstec_tree=_with_doy_days(abstec_tree),
            parquet_tecsuite_tree=_with_doy_days(parquet_tecsuite_tree),
            parquet_abstec_tree=_with_doy_days(parquet_abstec_tree),
            refresh=refresh,
        ),
    )
    return apply_lang_cookie(request, response)
