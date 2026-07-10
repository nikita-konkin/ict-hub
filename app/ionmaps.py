"""
ionmaps.py — IonMaps section: regional ionosphere (TEC) maps UI.

The map-building form lived on the Analysis page originally; it grew into its
own section (snapshot/animation modes, derived fields, kriging, LOSO
validation), so it now has a dedicated page. The data endpoints stay under
/tec-map/* and, like this page, are gated by the "analysis" page permission —
moving the UI must not change anyone's access.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.auth import require_page_access
from app.i18n import apply_lang_cookie, template_context
from app.models import User
from app.registry import CONVERTERS

router = APIRouter(tags=["ionmaps"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/ionmaps", response_class=HTMLResponse)
async def ionmaps_page(
    request: Request,
    current_user: User = Depends(require_page_access("analysis")),
):
    """IonMaps page: build VTEC / GDD / B_k maps from TEC-suite parquet data."""
    response = templates.TemplateResponse(
        "ionmaps.html",
        template_context(
            request,
            current_user=current_user,
            converters=CONVERTERS,
        ),
    )
    return apply_lang_cookie(request, response)
