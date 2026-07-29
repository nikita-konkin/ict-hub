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
import threading
import time
from datetime import datetime, timezone
import json

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import bcrypt
from sqlalchemy.orm import Session

from app import audit
from app import config as cfg
from app.database import get_db
from app.i18n import apply_lang_cookie, get_lang, template_context, translate
from app.models import AuditLog, User
from app.registry import CONVERTERS

logger = logging.getLogger(__name__)
router = APIRouter(tags=["auth"])

templates = Jinja2Templates(directory="app/templates")

# Minimum length enforced when a user sets/changes a password via the app.
MIN_PASSWORD_LENGTH = 8

# Precomputed bcrypt hash used to equalise timing when the username does not
# exist, so an attacker cannot distinguish "no such user" from "wrong password"
# by response time (username-enumeration defense).
_DUMMY_PW_HASH = bcrypt.hashpw(b"timing-equalizer-not-a-real-password", bcrypt.gensalt())

# ── In-memory login rate limiter ──────────────────────────────────────────────
# Keyed by (client_ip, username). Sufficient for the single-worker deployment
# this app ships as; swap for a shared store if you scale to multiple workers.
_login_attempts_lock = threading.Lock()
_login_attempts: dict[tuple[str, str], list[float]] = {}


def _rate_key(ip: str | None, username: str) -> tuple[str, str]:
    return (ip or "?", (username or "").strip().lower())


def login_is_locked(ip: str | None, username: str) -> bool:
    """True if this (ip, username) has exceeded the failed-attempt threshold."""
    if not cfg.LOGIN_RATE_LIMIT_ENABLED:
        return False
    now = time.monotonic()
    window = cfg.LOGIN_RATE_LIMIT_WINDOW_SEC
    key = _rate_key(ip, username)
    with _login_attempts_lock:
        recent = [t for t in _login_attempts.get(key, []) if now - t < window]
        _login_attempts[key] = recent
        return len(recent) >= cfg.LOGIN_RATE_LIMIT_MAX_ATTEMPTS


def _record_login_failure(ip: str | None, username: str) -> None:
    if not cfg.LOGIN_RATE_LIMIT_ENABLED:
        return
    now = time.monotonic()
    key = _rate_key(ip, username)
    with _login_attempts_lock:
        _login_attempts.setdefault(key, []).append(now)
        # Bound memory: opportunistically purge stale buckets.
        if len(_login_attempts) > 4096:
            cutoff = now - cfg.LOGIN_RATE_LIMIT_WINDOW_SEC
            for k in list(_login_attempts.keys()):
                fresh = [t for t in _login_attempts[k] if t > cutoff]
                if fresh:
                    _login_attempts[k] = fresh
                else:
                    del _login_attempts[k]


def _clear_login_failures(ip: str | None, username: str) -> None:
    with _login_attempts_lock:
        _login_attempts.pop(_rate_key(ip, username), None)


def new_account_permissions(
    *,
    allow_tec_suite: bool = False,
    allow_dat_parquet: bool = False,
    allow_abstec_suite: bool = False,
) -> dict:
    """
    Default rules for newly created (non-admin) accounts.

    Rule set requested:
      1) Access only to Overview pages: Data analysis, Stations Map, and Indexed data
      2) Distinct access toggles to TEC-Suite, DAT <-> Parquet, AbsTEC Suite
    """
    return {
        "pages": {
            "dashboard": False,
            "history": False,
            "analysis": True,
            "stations_map": True,
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

    # If the account is flagged to rotate a weak/default password, funnel every
    # request to the change-password page until it is done. Allow only the pages
    # needed to complete (or abandon) that flow to avoid a redirect loop.
    if getattr(user, "must_change_password", False):
        path = request.url.path
        if not (
            path == "/account/password"
            or path == "/logout"
            or path == "/login"
            or path.startswith("/static/")
        ):
            raise HTTPException(
                status_code=status.HTTP_303_SEE_OTHER,
                headers={"Location": "/account/password"},
            )
    return user


def get_current_user_or_401(request: Request, db: Session = Depends(get_db)) -> User:
    """
    Like `get_current_user`, but returns a JSON-friendly 401 instead of a 303 redirect.

    Use this for API endpoints that are consumed via `fetch()` where an HTML redirect
    would be confusing to handle client-side.
    """
    try:
        return get_current_user(request, db)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_303_SEE_OTHER:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated") from exc
        raise


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
    ip = audit.client_ip(request)
    lang = get_lang(request)

    # Too many recent failures for this (IP, username) → refuse without even
    # checking the password, to blunt brute-force / credential-stuffing.
    if login_is_locked(ip, username):
        logger.warning("Login locked out for username=%r from ip=%s", username, ip)
        audit.record(db, "login.locked", request=request, actor_username=username,
                     detail="too many failed attempts")
        response = templates.TemplateResponse(
            "login.html",
            template_context(
                request,
                error=translate(lang, "auth_too_many_attempts"),
                converters=CONVERTERS,
            ),
            status_code=429,
        )
        return apply_lang_cookie(request, response)

    user = db.query(User).filter(User.username == username).first()

    # Constant-ish work whether or not the user exists (enumeration defense).
    if user is not None:
        password_ok = verify_password(password, user.hashed_pw)
    else:
        bcrypt.checkpw(password.encode("utf-8"), _DUMMY_PW_HASH)
        password_ok = False

    if not user or not password_ok:
        _record_login_failure(ip, username)
        logger.warning("Failed login attempt for username=%r from ip=%s", username, ip)
        audit.record(db, "login.failed", request=request, actor_username=username)
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
        audit.record(db, "login.failed", request=request, actor=user,
                     detail="account deactivated")
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

    # Success — clear the failure counter and open a session.
    _clear_login_failures(ip, username)
    request.session["user_id"] = user.id
    user.last_login = datetime.now(timezone.utc)
    db.commit()
    logger.info("User %r logged in from ip=%s", username, ip)
    audit.record(db, "login.success", request=request, actor=user)

    if getattr(user, "must_change_password", False):
        return RedirectResponse("/account/password", status_code=302)
    return RedirectResponse(user.default_landing_path(), status_code=302)


@router.get("/logout")
async def logout(request: Request, db: Session = Depends(get_db)):
    """Clear the session and redirect to the login page."""
    uid = request.session.get("user_id")
    request.session.clear()
    if uid:
        user = db.query(User).filter(User.id == uid).first()
        audit.record(db, "logout", request=request, actor=user,
                     actor_username=(user.username if user else None))
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
    audit.record(db, "user.create", request=request, actor=admin, target=username,
                 detail=f"role={role}")
    return RedirectResponse("/users", status_code=302)


@router.post("/users/{user_id}/toggle")
async def toggle_user(
    user_id: int,
    request: Request,
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
    audit.record(db, "user.toggle", request=request, actor=admin, target=user.username,
                 detail=f"is_active={user.is_active}")
    return RedirectResponse("/users", status_code=302)


# ─────────────────────────────────────────────────────────────────────────────
# Self-service password change (also the forced-rotation landing page)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/account/password", response_class=HTMLResponse)
async def change_password_form(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """Render the change-password form for the logged-in user."""
    response = templates.TemplateResponse(
        "account_password.html",
        template_context(
            request,
            current_user=current_user,
            must_change=bool(getattr(current_user, "must_change_password", False)),
            error=None,
            success=False,
            converters=CONVERTERS,
        ),
    )
    return apply_lang_cookie(request, response)


@router.post("/account/password", response_class=HTMLResponse)
async def change_password_submit(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Validate and apply a password change for the logged-in user."""
    lang = get_lang(request)

    def _render(error: str | None = None, success: bool = False, status_code: int = 200):
        response = templates.TemplateResponse(
            "account_password.html",
            template_context(
                request,
                current_user=current_user,
                must_change=bool(getattr(current_user, "must_change_password", False)) and not success,
                error=error,
                success=success,
                converters=CONVERTERS,
            ),
            status_code=status_code,
        )
        return apply_lang_cookie(request, response)

    if not verify_password(current_password, current_user.hashed_pw):
        audit.record(db, "password.change_failed", request=request, actor=current_user,
                     detail="wrong current password")
        return _render(error=translate(lang, "acct_err_current_wrong"), status_code=400)

    if new_password != confirm_password:
        return _render(error=translate(lang, "acct_err_mismatch"), status_code=400)

    if len(new_password) < MIN_PASSWORD_LENGTH:
        return _render(error=translate(lang, "acct_err_too_short"), status_code=400)

    if verify_password(new_password, current_user.hashed_pw):
        return _render(error=translate(lang, "acct_err_same"), status_code=400)

    current_user.hashed_pw = hash_password(new_password)
    current_user.must_change_password = False
    db.commit()
    logger.info("User %r changed their password", current_user.username)
    audit.record(db, "password.change", request=request, actor=current_user)
    return _render(success=True)


# ─────────────────────────────────────────────────────────────────────────────
# Audit log (admin-only accounting view)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/audit", response_class=HTMLResponse)
async def audit_log_view(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    """Admin-only: recent security/accounting events, newest first."""
    entries = (
        db.query(AuditLog)
        .order_by(AuditLog.id.desc())
        .limit(500)
        .all()
    )
    response = templates.TemplateResponse(
        "audit.html",
        template_context(request, entries=entries, current_user=admin, converters=CONVERTERS),
    )
    return apply_lang_cookie(request, response)
