"""
indexer_progress.py — Data-indexer progress banner.

A full rescan of a large tree (RINEX above all) takes minutes, during which
year/day/station lists load slowly or come back empty. The layout polls this
fragment so users see what is being indexed and roughly how long to wait; the
estimate is the duration of the previous scan of the same folder.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.auth import get_current_user_or_401
from app.data_indexer_client import get_indexer_scans_async
from app.i18n import get_lang, template_context, translate
from app.models import User

router = APIRouter(tags=["indexer-progress"])
templates = Jinja2Templates(directory="app/templates")

# Background refreshes of small trees finish in a few seconds; showing them
# would only flash the banner on every cache expiry.
_MIN_VISIBLE_SECONDS = 5.0

_TYPE_KEYS = {
    "rinex": "indexer_progress_type_rinex",
    "tecsuite": "indexer_progress_type_tecsuite",
    "abstec": "indexer_progress_type_abstec",
    "parquet": "indexer_progress_type_parquet",
    "parquet_sat": "indexer_progress_type_parquet_sat",
}


def _seconds(value: object) -> float | None:
    return max(0.0, float(value)) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _duration(lang: str, seconds: float) -> str:
    total = round(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    unit = {name: translate(lang, f"indexer_progress_unit_{name}") for name in ("h", "min", "s")}
    if hours:
        return f"{hours} {unit['h']} {minutes:02d} {unit['min']}"
    if minutes:
        return f"{minutes} {unit['min']} {secs:02d} {unit['s']}"
    return f"{secs} {unit['s']}"


def _row(lang: str, scan: dict[str, Any]) -> dict[str, Any] | None:
    elapsed = _seconds(scan.get("elapsed_seconds"))
    if elapsed is None or elapsed < _MIN_VISIBLE_SECONDS:
        return None
    expected = _seconds(scan.get("expected_seconds"))
    root = str(scan.get("root") or "")
    kind = str(scan.get("type") or "")
    row: dict[str, Any] = {
        "label": translate(lang, _TYPE_KEYS[kind]) if kind in _TYPE_KEYS else kind,
        "folder": root.rstrip("/\\").replace("\\", "/").rsplit("/", 1)[-1],
        "percent": None,
    }
    spent = _duration(lang, elapsed)
    if not expected:
        row["timing"] = translate(lang, "indexer_progress_first", elapsed=spent)
    elif elapsed > expected:
        row["timing"] = translate(lang, "indexer_progress_overrun", elapsed=spent, expected=_duration(lang, expected))
    else:
        row["timing"] = translate(lang, "indexer_progress_of_about", elapsed=spent, expected=_duration(lang, expected))
        row["percent"] = min(99, int(elapsed / expected * 100))
    return row


@router.get("/indexer/progress", response_class=HTMLResponse)
async def indexer_progress(request: Request, current_user: User = Depends(get_current_user_or_401)):
    lang = get_lang(request)
    rows = [row for row in (_row(lang, scan) for scan in await get_indexer_scans_async()) if row]
    return templates.TemplateResponse(
        request, "_indexer_progress.html", template_context(request, {"current_user": current_user, "scans": rows})
    )
