"""
feedback.py — Lightweight user feedback / bug report capture.

Variant A: a quick in-page widget for logged-in users, with an admin-only
page to review all submissions.
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.exc import OperationalError
from sqlalchemy import func

from app.auth import get_admin_user, get_current_user
from app.database import engine, get_db
from app.i18n import apply_lang_cookie, get_lang, template_context, translate
from app.models import FeedbackReport, User
from app.registry import CONVERTERS

logger = logging.getLogger(__name__)
router = APIRouter(tags=["feedback"])
templates = Jinja2Templates(directory="app/templates")

_ALLOWED_CATEGORIES = {
    "feedback": "feedback",
    "bug": "bug",
    "idea": "idea",
    "content": "content",
    "question": "question",
}


def _normalise_category(raw: str) -> str:
    value = str(raw or "").strip().lower()
    return _ALLOWED_CATEGORIES.get(value, "feedback")


@router.post("/feedback", response_class=HTMLResponse)
async def submit_feedback(
    request: Request,
    category: str = Form("feedback"),
    message: str = Form(""),
    page_url: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    wants_json = "application/json" in str(request.headers.get("accept", "")).lower()
    text = str(message or "").strip()
    if not text:
        if wants_json:
            lang = get_lang(request)
            return JSONResponse(
                {"ok": False, "error": translate(lang, "feedback_error_required")},
                status_code=400,
            )
        response = templates.TemplateResponse(
            "feedback_result.html",
            template_context(
                request,
                ok=False,
                error="Message is required.",
                converters=CONVERTERS,
            ),
            status_code=400,
        )
        return apply_lang_cookie(request, response)

    if len(text) > 4000:
        if wants_json:
            lang = get_lang(request)
            return JSONResponse(
                {"ok": False, "error": translate(lang, "feedback_error_too_long")},
                status_code=400,
            )
        response = templates.TemplateResponse(
            "feedback_result.html",
            template_context(
                request,
                ok=False,
                error="Message is too long (max 4000 characters).",
                converters=CONVERTERS,
            ),
            status_code=400,
        )
        return apply_lang_cookie(request, response)

    report = FeedbackReport(
        user_id=current_user.id,
        category=_normalise_category(category),
        message=text,
        page_url=str(page_url or "").strip() or None,
        user_agent=str(request.headers.get("user-agent", "")).strip()[:256] or None,
        status="new",
    )

    try:
        db.add(report)
        db.commit()
        db.refresh(report)
    except OperationalError as exc:
        # Self-heal on upgraded deployments where the new table may not exist yet.
        # (e.g., dev hot reload / container not restarted).
        msg = str(exc).lower()
        if "no such table" in msg and "feedback_reports" in msg:
            try:
                from app.models import Base
                Base.metadata.create_all(bind=engine)
                db.rollback()
                db.add(report)
                db.commit()
                db.refresh(report)
            except Exception:
                raise
        else:
            raise

    logger.info("Feedback submitted: user=%s category=%s id=%s", current_user.username, report.category, report.id)

    if wants_json:
        lang = get_lang(request)
        return JSONResponse(
            {"ok": True, "report_id": report.id, "message": translate(lang, "feedback_thanks")},
            status_code=200,
        )

    response = templates.TemplateResponse(
        "feedback_result.html",
        template_context(request, ok=True, report_id=report.id, converters=CONVERTERS),
    )
    return apply_lang_cookie(request, response)


@router.get("/feedback", response_class=HTMLResponse)
async def feedback_admin(
    request: Request,
    status_filter: str | None = None,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    query = (
        db.query(FeedbackReport)
        .options(joinedload(FeedbackReport.user))
        .order_by(FeedbackReport.created_at.desc())
    )
    filt = str(status_filter or "").strip().lower()
    if filt in {"new", "seen"}:
        query = query.filter(FeedbackReport.status == filt)

    reports = query.limit(500).all()
    total_reports = db.query(func.count(FeedbackReport.id)).scalar() or 0
    total_new = db.query(func.count(FeedbackReport.id)).filter(FeedbackReport.status == "new").scalar() or 0
    response = templates.TemplateResponse(
        "feedback_admin.html",
        template_context(
            request,
            reports=reports,
            current_user=admin,
            converters=CONVERTERS,
            status_filter=filt,
            total_reports=total_reports,
            total_new=total_new,
            database_url=str(engine.url),
        ),
    )
    return apply_lang_cookie(request, response)


@router.post("/feedback/{report_id}/seen")
async def mark_feedback_seen(
    report_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    report = db.query(FeedbackReport).filter(FeedbackReport.id == report_id).first()
    if not report:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Feedback report not found")
    report.status = "seen"
    db.commit()
    return RedirectResponse("/feedback", status_code=302)


@router.post("/feedback/{report_id}/unseen")
async def mark_feedback_unseen(
    report_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    report = db.query(FeedbackReport).filter(FeedbackReport.id == report_id).first()
    if not report:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Feedback report not found")
    report.status = "new"
    db.commit()
    return RedirectResponse("/feedback", status_code=302)


@router.get("/feedback/{report_id}.xml")
async def download_feedback_xml(
    report_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    report = (
        db.query(FeedbackReport)
        .options(joinedload(FeedbackReport.user))
        .filter(FeedbackReport.id == report_id)
        .first()
    )
    if not report:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Feedback report not found")

    root = ET.Element(
        "feedback_report",
        attrib={
            "id": str(report.id),
            "created_at": report.created_at.isoformat() if report.created_at else "",
            "status": str(report.status or ""),
            "category": str(report.category or ""),
        },
    )
    ET.SubElement(
        root,
        "user",
        attrib={
            "id": str(report.user_id),
            "username": str(report.user.username) if report.user else "",
            "role": str(report.user.role) if report.user else "",
        },
    )

    page_el = ET.SubElement(root, "page_url")
    page_el.text = str(report.page_url or "")

    ua_el = ET.SubElement(root, "user_agent")
    ua_el.text = str(report.user_agent or "")

    msg_el = ET.SubElement(root, "message")
    msg_el.text = str(report.message or "")

    xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    headers = {
        "Content-Disposition": f'attachment; filename="feedback_report_{report.id}.xml"',
    }
    return Response(content=xml_bytes, media_type="application/xml", headers=headers)
