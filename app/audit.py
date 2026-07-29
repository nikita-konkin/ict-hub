"""
audit.py — append-only security/accounting trail helper.

`record()` writes one AuditLog row and commits. It is deliberately best-effort:
an audit failure must never break the request that triggered it, so all errors
are swallowed and logged. We capture the direct socket peer as the IP and do NOT
trust X-Forwarded-For (client-spoofable unless a trusted proxy rewrites it).
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import Request
from sqlalchemy.orm import Session

from app.models import AuditLog, User

logger = logging.getLogger(__name__)


def client_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    client = getattr(request, "client", None)
    host = getattr(client, "host", None) if client else None
    return str(host)[:64] if host else None


def user_agent(request: Request | None) -> str | None:
    if request is None:
        return None
    ua = request.headers.get("user-agent")
    return ua[:256] if ua else None


def record(
    db: Session,
    action: str,
    *,
    request: Request | None = None,
    actor: Optional[User] = None,
    actor_username: str | None = None,
    target: str | None = None,
    detail: str | None = None,
) -> None:
    """Append one audit entry. Never raises into the caller."""
    try:
        entry = AuditLog(
            action=action,
            actor_user_id=(actor.id if actor is not None else None),
            actor_username=(actor.username if actor is not None else actor_username),
            target=(str(target)[:128] if target is not None else None),
            detail=(str(detail)[:2000] if detail is not None else None),
            ip=client_ip(request),
            user_agent=user_agent(request),
        )
        db.add(entry)
        db.commit()
    except Exception as exc:  # audit must never break a request
        logger.warning("Failed to write audit log (%s): %s", action, exc)
        try:
            db.rollback()
        except Exception:
            pass
