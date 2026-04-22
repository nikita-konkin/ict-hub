"""
auth.py — Authentication router, session dependency, and user management.

Strategy: signed session cookies via Starlette's SessionMiddleware (backed by
itsdangerous). No JWTs, no external auth provider — just a simple username/
password stored as a bcrypt hash in SQLite.

Routes:
  GET  /login           — render login form
  POST /login           — validate credentials, set session cookie
  GET  /logout          — clear session, redirect to login
  GET  /users           — list all users (admin only)
  POST /users           — create a user (admin only)
  POST /users/{id}/toggle — activate/deactivate a user (admin only)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
import json

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import bcrypt
from sqlalchemy.orm import Session

from app.database import get_db
from app.i18n import apply_lang_cookie, get_lang, template_context, translate
from app.models import User
from app.registry import CONVERTERS

logger = logging.getLogger(__name__)
router = APIRouter(tags=["auth"])

templates = Jinja2Templates(directory="app/templates")


def new_account_permissions(
    *,
    allow_tec_suite: bool = False,
    allow_dat_parquet: bool = False,
    allow_abstec_suite: bool = False,
) -> dict:
    """
    Default rules for newly created (non-admin) accounts.

    Rule set requested:
      1) Access only to Overview pages: Data analysis and Indexed data
      2) Distinct access toggles to TEC-Suite, DAT <-> Parquet, AbsTEC Suite
    """
    return {
        "pages": {
            "dashboard": False,
            "history": False,
            "analysis": True,
            "indexed_data": True,
        },
        "converters": {
            "tec-suite": bool(allow_tec_suite),
            "dat-parquet-handler": bool(allow_dat_parquet),
            "abstec-suite": bool(allow_abstec_suite),
        },
    }


def require_page_access(page: str):
    def _dep(current_user: User = Depends(get_current_user)) -> User:
        if getattr(current_user, "is_admin", False):
            return current_user
        if hasattr(current_user, "can_access_page") and current_user.can_access_page(page):
            return current_user
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": current_user.default_landing_path() if hasattr(current_user, "default_landing_path") else "/login"},
        )

    return _dep


def require_converter_access():
    def _dep(
        converter_name: str,
        current_user: User = Depends(get_current_user),
    ) -> User:
        if getattr(current_user, "is_admin", False):
            return current_user
        if hasattr(current_user, "can_access_converter") and current_user.can_access_converter(converter_name):
            return current_user
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": current_user.default_landing_path() if hasattr(current_user, "default_landing_path") else "/login"},
        )

    return _dep


# ─────────────────────────────────────────────────────────────────────────────
# Dependencies  (used in other routers via Depends)
# ─────────────────────────────────────────────────────────────────────────────

def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """
    FastAPI dependency. Reads the signed session cookie and returns the
    corresponding User object. Redirects to /login if no valid session exists.

    Raise an HTTPException with status 303 (redirect) rather than 401 so the
    browser navigates to the login page instead of showing a JSON error.
    """
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/login"},
        )
    user = db.query(User).filter(User.id == user_id, User.is_active == True).first()
    if not user:
        request.session.clear()
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/login"},
        )
    return user


def get_admin_user(current_user: User = Depends(get_current_user)) -> User:
    """
    Extends get_current_user to additionally require admin role.
    Returns 403 Forbidden to non-admins.
    """
    if not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")
    return current_user


# ─────────────────────────────────────────────────────────────────────────────
# Password helpers
# ─────────────────────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    # bcrypt.hashpw requires bytes input and returns bytes; we store as str in the DB
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    # Both sides must be bytes for bcrypt.checkpw
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    """Render the login page. Redirect to dashboard if already logged in."""
    if request.session.get("user_id"):
        return RedirectResponse("/", status_code=302)
    response = templates.TemplateResponse(
        "login.html",
        template_context(request, error=None, converters=CONVERTERS),
    )
    return apply_lang_cookie(request, response)


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    """Validate credentials, create session, and redirect to dashboard."""
    user = db.query(User).filter(User.username == username).first()

    if not user or not verify_password(password, user.hashed_pw):
        logger.warning("Failed login attempt for username=%r", username)
        lang = get_lang(request)
        response = templates.TemplateResponse(
            "login.html",
            template_context(
                request,
                error=translate(lang, "auth_invalid_credentials"),
                converters=CONVERTERS,
            ),
            status_code=401,
        )
        return apply_lang_cookie(request, response)

    if not user.is_active:
        lang = get_lang(request)
        response = templates.TemplateResponse(
            "login.html",
            template_context(
                request,
                error=translate(lang, "auth_account_deactivated"),
                converters=CONVERTERS,
            ),
            status_code=403,
        )
        return apply_lang_cookie(request, response)

    # Write user_id into the signed session cookie
    request.session["user_id"] = user.id
    user.last_login = datetime.now(timezone.utc)
    db.commit()
    logger.info("User %r logged in", username)

    return RedirectResponse(user.default_landing_path(), status_code=302)


@router.get("/logout")
async def logout(request: Request):
    """Clear the session and redirect to the login page."""
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


@router.get("/users", response_class=HTMLResponse)
async def users_list(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    """Admin-only: list all users with their roles and status."""
    users = db.query(User).order_by(User.created_at).all()
    response = templates.TemplateResponse(
        "users.html",
        template_context(request, users=users, current_user=admin, converters=CONVERTERS),
    )
    return apply_lang_cookie(request, response)


@router.post("/users", response_class=HTMLResponse)
async def create_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form("operator"),
    allow_tec_suite: str | None = Form(None),
    allow_dat_parquet: str | None = Form(None),
    allow_abstec_suite: str | None = Form(None),
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    """Admin-only: create a new user."""
    existing = db.query(User).filter(User.username == username).first()
    if existing:
        users = db.query(User).order_by(User.created_at).all()
        response = templates.TemplateResponse(
            "users.html",
            template_context(
                request,
                users=users,
                current_user=admin,
                error=f"Username '{username}' is already taken.",
                converters=CONVERTERS,
            ),
            status_code=400,
        )
        return apply_lang_cookie(request, response)

    if role not in ("admin", "operator"):
        role = "operator"

    permissions_json = ""
    if role != "admin":
        perms = new_account_permissions(
            allow_tec_suite=bool(allow_tec_suite),
            allow_dat_parquet=bool(allow_dat_parquet),
            allow_abstec_suite=bool(allow_abstec_suite),
        )
        permissions_json = json.dumps(perms, ensure_ascii=False)

    new_user = User(
        username=username,
        hashed_pw=hash_password(password),
        role=role,
        permissions_json=permissions_json,
    )
    db.add(new_user)
    db.commit()
    logger.info("Admin %r created user %r (role=%s)", admin.username, username, role)
    return RedirectResponse("/users", status_code=302)


@router.post("/users/{user_id}/toggle")
async def toggle_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    """Admin-only: flip a user's is_active flag."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="Cannot deactivate yourself")
    user.is_active = not user.is_active
    db.commit()
    return RedirectResponse("/users", status_code=302)
