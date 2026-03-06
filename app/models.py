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


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    hashed_pw: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="operator")
    # "operator" can run jobs and see their own history
    # "admin" can manage users and see everyone's history
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)
    last_login: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationship: one user → many job runs
    job_runs: Mapped[list["JobRun"]] = relationship("JobRun", back_populates="user")

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


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
