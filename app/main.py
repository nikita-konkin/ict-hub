"""
main.py — FastAPI application factory and startup.

This is the entry point Uvicorn runs. It wires together all the middleware,
routers, static files, and templates, then performs first-boot initialisation
(creating database tables and a default admin user if none exist).
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import urllib.parse

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app import analysis, auth, feedback, indexed_data, ionmaps, jobs, stations_map, tec_map
from app import config as cfg
from app.auth import hash_password, verify_password
from app.config import ADMIN_PASSWORD, SECRET_KEY
from app.database import SessionLocal, engine
from app.job_runtime import start_job_runtime, stop_job_runtime
from app.models import Base, User

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Lifespan (replaces deprecated @app.on_event("startup"))
# ─────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Code inside the 'with' block runs at startup; code after 'yield' at shutdown.
    We use this to create the database tables and seed the admin user exactly once.
    """
    logger.info("Creating database tables if they don't exist…")
    Base.metadata.create_all(bind=engine)

    # Lightweight schema migration: add users.permissions_json if missing.
    # This project intentionally avoids Alembic; we keep migrations minimal.
    from sqlalchemy import text

    with engine.begin() as conn:
        try:
            cols = [row[1] for row in conn.execute(text("PRAGMA table_info(users)")).all()]
        except Exception:
            cols = []
        if "permissions_json" not in cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN permissions_json TEXT NOT NULL DEFAULT ''"))
        if "must_change_password" not in cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN must_change_password BOOLEAN NOT NULL DEFAULT 0"))

        # Ensure feedback_reports exists for upgraded deployments (no Alembic).
        # Base.metadata.create_all should create it, but we keep this guard so
        # a long-running dev server can accept feedback immediately after code
        # hot-reload without requiring a manual restart.
        try:
            tables = [row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).all()]
        except Exception:
            tables = []
        if "feedback_reports" not in tables:
            Base.metadata.create_all(bind=engine)

        # Performance/size guardrails for large job-event tables.
        # This helps pruning queries avoid full-table scans.
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_job_events_job_type_id "
                "ON job_events(job_id, event_type, id)"
            )
        )

    # If no users exist at all, create a default admin so the system is usable
    # immediately after first boot. The admin can then create other accounts.
    db = SessionLocal()
    try:
        user_count = db.query(User).count()
        if user_count == 0:
            weak = cfg.is_weak_admin_password(ADMIN_PASSWORD)
            admin = User(
                username="admin",
                hashed_pw=hash_password(ADMIN_PASSWORD),
                role="admin",
                # Force a change on first login when seeded with a weak/default password.
                must_change_password=weak,
            )
            db.add(admin)
            db.commit()
            if weak:
                logger.warning(
                    "First boot: created default admin with a WEAK/default password "
                    "(ADMIN_PASSWORD). The admin will be forced to set a new password "
                    "on first login. Set a strong ADMIN_PASSWORD before first boot to avoid this."
                )
            else:
                logger.info("First boot: created default admin user from ADMIN_PASSWORD.")

        # Upgrade path: if the configured ADMIN_PASSWORD is still weak/default and
        # an existing admin account still uses it, force a rotation on next login.
        if cfg.is_weak_admin_password(ADMIN_PASSWORD):
            flagged = False
            for admin_u in db.query(User).filter(User.role == "admin").all():
                if not admin_u.must_change_password and verify_password(ADMIN_PASSWORD, admin_u.hashed_pw):
                    admin_u.must_change_password = True
                    flagged = True
                    logger.warning(
                        "Admin %r still uses the default/weak password; flagged for a "
                        "forced password change on next login.", admin_u.username
                    )
            if flagged:
                db.commit()
    finally:
        db.close()

    await start_job_runtime()
    yield  # Application runs

    await stop_job_runtime()
    logger.info("Shutting down.")


# ─────────────────────────────────────────────────────────────────────────────
# Application
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="ConverterHub",
    description="Local-network web interface for Docker-based data converters.",
    lifespan=lifespan,
)

# SessionMiddleware signs the session cookie with SECRET_KEY.
# https_only is env-driven (SESSION_COOKIE_SECURE): OFF by default so plain-HTTP
# local deployments keep working, ON when served over HTTPS behind a TLS proxy.
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    session_cookie="ch_session",
    max_age=cfg.SESSION_MAX_AGE_SEC,
    https_only=cfg.SESSION_COOKIE_SECURE,
    same_site="lax",
)

# Compress responses over 1KB. base.html alone is ~64KB of inline CSS/JS
# (every page ships it, since there's no SPA routing) — gzip cuts that to ~13KB.
app.add_middleware(GZipMiddleware, minimum_size=1000)


# ── Security headers + CSRF (Origin) guard ────────────────────────────────────
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


def _request_is_same_origin(request: Request) -> bool:
    """
    Compare the browser-set Origin (or Referer) host to the Host header. Browsers
    always send Origin on cross-site POSTs, so a mismatch is a CSRF attempt.
    Requests with neither header (non-browser clients, server-to-server) are
    allowed — CSRF is a browser-only threat.
    """
    host = request.headers.get("host")
    for header in ("origin", "referer"):
        value = request.headers.get(header)
        if value:
            try:
                netloc = urllib.parse.urlsplit(value).netloc
            except Exception:
                return False
            return bool(netloc) and netloc == host
    return True


def _apply_security_headers(request: Request, response) -> None:
    h = response.headers
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("Referrer-Policy", "same-origin")
    h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    # The UI ships inline CSS/JS (no SPA bundle), so 'unsafe-inline'/'unsafe-eval'
    # are required for it to function; the real wins here are frame-ancestors
    # (clickjacking) and base-uri/object-src lockdown. Tighten once assets are
    # externalised with hashes/nonces.
    h.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "img-src 'self' data: blob: http: https:; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
        "connect-src 'self'; font-src 'self' data:; "
        "frame-ancestors 'none'; base-uri 'self'; object-src 'none'",
    )
    if request.url.scheme == "https":
        h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    if cfg.CSRF_ORIGIN_CHECK_ENABLED and request.method not in _SAFE_METHODS:
        if not _request_is_same_origin(request):
            return PlainTextResponse("Cross-origin request blocked", status_code=403)
    response = await call_next(request)
    if cfg.SECURITY_HEADERS_ENABLED:
        _apply_security_headers(request, response)
    return response

# Ensure the static directory exists — Starlette will raise RuntimeError if it doesn't
import os as _os
_os.makedirs("app/static", exist_ok=True)

# Serve CSS / any future static assets
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Register routers
app.include_router(auth.router)
app.include_router(jobs.router)
app.include_router(analysis.router)
app.include_router(ionmaps.router)
app.include_router(indexed_data.router)
app.include_router(stations_map.router)
app.include_router(feedback.router)
app.include_router(tec_map.router)

# ─────────────────────────────────────────────────────────────────────────────
# API proxy routes for external services
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# Global exception handler for 303 redirects issued by get_current_user()
# ─────────────────────────────────────────────────────────────────────────────
# FastAPI by default turns HTTPExceptions into JSON responses. We need 303
# redirects (from the auth dependency) to actually redirect, not return JSON.

from fastapi import HTTPException
from fastapi.responses import RedirectResponse as _RR

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 303:
        location = exc.headers.get("Location", "/login")
        return _RR(url=location, status_code=303)
    # For all other HTTP errors, re-raise so FastAPI's default handler runs
    from fastapi.exception_handlers import http_exception_handler as _default
    return await _default(request, exc)
