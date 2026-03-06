"""
main.py — FastAPI application factory and startup.

This is the entry point Uvicorn runs. It wires together all the middleware,
routers, static files, and templates, then performs first-boot initialisation
(creating database tables and a default admin user if none exist).
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app import auth, jobs
from app.auth import hash_password
from app.config import ADMIN_PASSWORD, SECRET_KEY
from app.database import SessionLocal, engine
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

    # If no users exist at all, create a default admin so the system is usable
    # immediately after first boot. The admin can then create other accounts.
    db = SessionLocal()
    try:
        user_count = db.query(User).count()
        if user_count == 0:
            admin = User(
                username="admin",
                hashed_pw=hash_password(ADMIN_PASSWORD),
                role="admin",
            )
            db.add(admin)
            db.commit()
            logger.info(
                "First boot: created default admin user. "
                "Password from ADMIN_PASSWORD env var (default: 'admin'). "
                "Change it immediately via the Users page."
            )
    finally:
        db.close()

    yield  # Application runs

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
# max_age=86400 → sessions expire after 24 hours of inactivity.
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    session_cookie="ch_session",
    max_age=86400,
    https_only=False,  # local network — no TLS required
    same_site="lax",
)

# Ensure the static directory exists — Starlette will raise RuntimeError if it doesn't
import os as _os
_os.makedirs("app/static", exist_ok=True)

# Serve CSS / any future static assets
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Register routers
app.include_router(auth.router)
app.include_router(jobs.router)


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
