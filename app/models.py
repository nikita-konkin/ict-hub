"""
models.py — SQLAlchemy ORM models.

Two tables: User (authentication & role) and JobRun (audit log of every
container execution). Keeping them in one file makes the data schema easy
to understand at a glance.
"""
import json
from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer, String, Text, func
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _safe_json_loads(raw: str | None) -> dict | None:
    value = (raw or "").strip()
    if not value:
        return None
    try:
        loaded = json.loads(value)
    except Exception:
        return None
    return loaded if isinstance(loaded, dict) else None


def _boolish(value: object) -> bool:
    return bool(value) is True


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    hashed_pw: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="operator")
    # "operator" can run jobs and see their own history
    # "admin" can manage users and see everyone's history
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # JSON-encoded access control rules. Empty string => legacy "allow all".
    permissions_json: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)
    last_login: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationship: one user → many job runs
    job_runs: Mapped[list["JobRun"]] = relationship("JobRun", back_populates="user")

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def permissions(self) -> dict | None:
        """
        Return parsed permission dict or None.

        None means "legacy / unrestricted" access so existing deployments
        are not locked out when the column is introduced.
        """
        if self.is_admin:
            return None
        return _safe_json_loads(self.permissions_json)

    def can_access_page(self, page: str) -> bool:
        if self.is_admin:
            return True
        perms = self.permissions
        if perms is None:
            return True
        pages = perms.get("pages", {})
        return _boolish(pages.get(page, False))

    def can_access_converter(self, converter_name: str) -> bool:
        if self.is_admin:
            return True
        perms = self.permissions
        if perms is None:
            return True
        converters = perms.get("converters", {})
        return _boolish(converters.get(converter_name, False))

    def default_landing_path(self) -> str:
        """
        Pick a safe post-login landing page based on granted permissions.

        Order matters: we prefer "overview" pages so restricted accounts land
        somewhere meaningful.
        """
        if self.is_admin:
            return "/"

        if self.can_access_page("analysis"):
            return "/analysis"
        if self.can_access_page("indexed_data"):
            return "/indexed-data"
        if self.can_access_page("dashboard"):
            return "/"
        # If no pages are allowed but a converter is, fall back to it.
        perms = self.permissions or {}
        converters = perms.get("converters", {}) if isinstance(perms, dict) else {}
        for name, allowed in converters.items():
            if _boolish(allowed):
                return f"/run/{name}"
        return "/login"


class JobRun(Base):
    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)

    # Which converter was used (matches registry key, e.g. "tecsuite")
    converter: Mapped[str] = mapped_column(String(64), nullable=False)

    # Full CLI flags as JSON string so the exact invocation is reproducible
    flags_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    # Host-side volume paths entered by the user in the form
    rinex_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    output_path: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Docker container ID — used to stream logs via the SSE endpoint
    container_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Lifecycle timestamps
    started_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Container exit code: 0 = success, non-zero = failure, None = still running
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Human-readable status: "running" | "success" | "failed" | "error"
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")

    # Relationship back to user
    user: Mapped["User"] = relationship("User", back_populates="job_runs")
    events: Mapped[list["JobEvent"]] = relationship(
        "JobEvent",
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="JobEvent.id",
    )

    @property
    def flags(self) -> dict:
        """Deserialise the stored JSON flags for template rendering."""
        try:
            return json.loads(self.flags_json)
        except (json.JSONDecodeError, TypeError):
            return {}

    @property
    def duration_seconds(self) -> float | None:
        """Wall-clock duration in seconds, or None if the job is still running."""
        if self.finished_at and self.started_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None

    @property
    def status_class(self) -> str:
        """CSS class suffix used by the template for colour-coding status badges."""
        return {
            "running": "running",
            "success": "success",
            "failed": "danger",
            "error": "danger",
        }.get(self.status, "muted")


class JobEvent(Base):
    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    job_id: Mapped[int] = mapped_column(Integer, ForeignKey("job_runs.id"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_xml: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)

    job: Mapped["JobRun"] = relationship("JobRun", back_populates="events")
