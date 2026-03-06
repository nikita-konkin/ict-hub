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

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import bcrypt
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.registry import CONVERTERS

logger = logging.getLogger(__name__)
router = APIRouter(tags=["auth"])

templates = Jinja2Templates(directory="app/templates")


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
    return templates.TemplateResponse("login.html", {"request": request, "error": None, "converters": CONVERTERS})


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
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid username or password.", "converters": CONVERTERS},
            status_code=401,
        )

    if not user.is_active:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Account is deactivated. Contact an administrator.", "converters": CONVERTERS},
            status_code=403,
        )

    # Write user_id into the signed session cookie
    request.session["user_id"] = user.id
    user.last_login = datetime.now(timezone.utc)
    db.commit()
    logger.info("User %r logged in", username)

    return RedirectResponse("/", status_code=302)


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
    return templates.TemplateResponse(
        "users.html",
        {"request": request, "users": users, "current_user": admin, "converters": CONVERTERS},
    )


@router.post("/users", response_class=HTMLResponse)
async def create_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form("operator"),
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    """Admin-only: create a new user."""
    existing = db.query(User).filter(User.username == username).first()
    if existing:
        users = db.query(User).order_by(User.created_at).all()
        return templates.TemplateResponse(
            "users.html",
            {
                "request": request,
                "users": users,
                "current_user": admin,
                "error": f"Username '{username}' is already taken.",
                "converters": CONVERTERS,
            },
            status_code=400,
        )

    if role not in ("admin", "operator"):
        role = "operator"

    new_user = User(
        username=username,
        hashed_pw=hash_password(password),
        role=role,
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
