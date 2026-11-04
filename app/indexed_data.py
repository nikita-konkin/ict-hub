"""
indexed_data.py — Indexed data browser page.

Shows the folder trees currently indexed by the data-indexer service for:
  - RINEX
  - TEC-suite DAT output
  - AbsTEC output
  - Parquet outputs (TEC-suite / AbsTEC)
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import audit
from app import config as cfg
from app.auth import get_admin_user, require_page_access
from app.data_indexer_client import (
    clear_cache as clear_data_indexer_cache,
)
from app.data_indexer_client import (
    list_abstec_output_structure_async,
    list_parquet_output_structure_async,
    list_rinex_server_structure_async,
    list_tecsuite_output_structure_async,
)
from app.database import get_db
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
        days_raw = year.get("days")
        for day in days_raw if isinstance(days_raw, list) else []:
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
        "parquet_abstec": _scan_root(
            cfg.PARQUET_OUTPUT_ABSTEC_DATA_PATH_CONTAINER, cfg.PARQUET_OUTPUT_ABSTEC_DATA_PATH_HOST
        ),
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
        request,
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


def _removal_targets(
    year: str, day_from: int, day_to: int, stations: str, kind: str = "parquet"
) -> tuple[Path, list[Path]]:
    if kind == "dat":
        root_text = cfg.ABSTEC_OUTPUT_DATA_PATH_CONTAINER.strip() or cfg.ABSTEC_OUTPUT_DATA_PATH_HOST.strip()
    elif kind == "parquet":
        root_text = (
            cfg.PARQUET_OUTPUT_ABSTEC_DATA_PATH_CONTAINER.strip() or cfg.PARQUET_OUTPUT_ABSTEC_DATA_PATH_HOST.strip()
        )
    else:
        raise HTTPException(400, "Invalid output type")
    if not root_text:
        raise HTTPException(400, "AbsTEC output root is not configured")
    if not re.fullmatch(r"[0-9]{4}", year) or not (1 <= day_from <= day_to <= 366):
        raise HTTPException(400, "Choose a valid year and day range")
    names = sorted(set(stations.lower().split()))
    if not names or any(not re.fullmatch(r"[a-z0-9]{4}", name) for name in names):
        raise HTTPException(400, "Enter four-character station codes separated by spaces")
    try:
        root = Path(root_text).resolve(strict=True)
    except OSError as exc:
        raise HTTPException(503, "AbsTEC output root is unavailable") from exc
    targets = []
    for day in range(day_from, day_to + 1):
        for station in names:
            path = root / year / f"{day:03d}" / station
            if any(part.is_symlink() for part in (path.parent.parent, path.parent, path)):
                raise HTTPException(400, "Symbolic links are not supported")
            if path.is_dir():
                targets.append(path)
    return root, targets


def _target_digest(root: Path, targets: list[Path]) -> str:
    entries = []
    for directory in targets:
        for path in sorted(directory.rglob("*")):
            if path.is_symlink():
                raise HTTPException(400, "Symbolic links are not supported")
            info = path.stat()
            entries.append(f"{path.relative_to(root)}:{info.st_size}:{info.st_mtime_ns}")
        info = directory.stat()
        entries.append(f"{directory.relative_to(root)}:{info.st_mtime_ns}")
    return hashlib.sha256("\n".join(entries).encode()).hexdigest()


def _selection(year: str, day_from: int, day_to: int, stations: str, output_type: str):
    kinds = {"dat": ("dat",), "parquet": ("parquet",), "both": ("dat", "parquet")}.get(output_type)
    if not kinds:
        raise HTTPException(400, "Invalid output type")
    selections = [(kind, *_removal_targets(year, day_from, day_to, stations, kind)) for kind in kinds]
    roots = [root for _, root, _ in selections]
    if len(roots) == 2 and (roots[0] == roots[1] or roots[0] in roots[1].parents or roots[1] in roots[0].parents):
        raise HTTPException(400, "DAT and Parquet roots must be separate")
    return selections


def _selection_digest(selections):
    return hashlib.sha256(
        "\n".join(f"{kind}:{_target_digest(root, targets)}" for kind, root, targets in selections).encode()
    ).hexdigest()


@router.get("/indexed-data/remove-abstec", response_class=HTMLResponse)
def preview_abstec_removal(
    request: Request,
    year: str = "",
    day_from: int = 1,
    day_to: int = 1,
    stations: str = "",
    output_type: str = "both",
    current_user: User = Depends(get_admin_user),
):
    targets = []
    digest = ""
    if year or stations:
        selections = _selection(year, day_from, day_to, stations, output_type)
        digest = _selection_digest(selections)
        targets = [f"{kind.upper()}: {path.relative_to(root)}" for kind, root, paths in selections for path in paths]
    return templates.TemplateResponse(
        request,
        "remove_abstec.html",
        template_context(
            request,
            current_user=current_user,
            converters=CONVERTERS,
            year=year,
            day_from=day_from,
            day_to=day_to,
            stations=stations,
            output_type=output_type,
            targets=targets,
            digest=digest,
        ),
    )


@router.post("/indexed-data/remove-abstec")
def remove_abstec_output(
    request: Request,
    year: str = Form(...),
    day_from: int = Form(...),
    day_to: int = Form(...),
    stations: str = Form(...),
    output_type: str = Form(...),
    digest: str = Form(...),
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    selections = _selection(year, day_from, day_to, stations, output_type)
    if not any(targets for _, _, targets in selections) or digest != _selection_digest(selections):
        raise HTTPException(409, "The selection changed. Preview it again.")
    batch_id = uuid.uuid4().hex
    moved = []
    try:
        for _kind, root, targets in selections:
            if not targets:
                continue
            trash = root / ".ionflow-trash"
            if trash.is_symlink():
                raise HTTPException(400, "Trash directory cannot be a symbolic link")
            batch = trash / batch_id
            batch.mkdir(parents=True)
            for path in targets:
                dest = batch / path.relative_to(root)
                dest.parent.mkdir(parents=True, exist_ok=True)
                path.rename(dest)
                moved.append((path, dest))
    except (OSError, HTTPException) as exc:
        for original, dest in reversed(moved):
            dest.rename(original)
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(500, f"Could not remove selection: {exc}") from exc
    audit.record(
        db,
        "data.remove",
        request=request,
        actor=current_user,
        target=f"AbsTEC {output_type}",
        detail=f"Moved {len(moved)} station folders to {batch_id}: {year} {day_from}-{day_to} {stations}",
    )
    clear_data_indexer_cache()
    return RedirectResponse("/indexed-data?refresh=1", status_code=303)
