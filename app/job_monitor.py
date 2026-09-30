"""
job_monitor.py — Records job outcomes independently of any browser.

Every started container gets a daemon thread that blocks on Docker's wait API
and writes the exit code to the JobRun row. Without it, a job's final status
was only saved while someone kept its SSE log stream open until the end.

On startup, reconcile_running_jobs() re-attaches watchers to jobs that were
still "running" when the app stopped, so their outcome is not lost either.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone

import docker.errors

from app.database import SessionLocal
from app.models import JobRun
from app.runner import wait_for_exit

logger = logging.getLogger(__name__)

_watched_jobs: set[int] = set()
_watched_lock = threading.Lock()


def record_job_exit(job_id: int, exit_code: int | None) -> None:
    """
    Persist a finished job's outcome, unless something already did.

    Only jobs still marked "running" are updated, so a user's stop (exit code -2)
    or a result already written by the SSE stream is never overwritten.
    exit_code None means the container vanished before its status could be
    read; that is recorded as an error rather than guessed.
    """
    db = SessionLocal()
    try:
        job = db.query(JobRun).filter(JobRun.id == job_id).first()
        if not job or job.status != "running":
            return
        job.finished_at = datetime.now(timezone.utc)
        if exit_code is None:
            job.status = "error"
            job.exit_code = -1
        else:
            job.status = "success" if exit_code == 0 else "failed"
            job.exit_code = exit_code
        db.commit()
        logger.info("Job %s finished: status=%s exit_code=%s", job_id, job.status, job.exit_code)
    finally:
        db.close()


def _watch_job(job_id: int, container_id: str, auto_remove: bool) -> None:
    try:
        exit_code = wait_for_exit(container_id, auto_remove=auto_remove)
    except docker.errors.DockerException as exc:
        # Daemon unreachable: leave the job as is; the next startup reconciles it.
        logger.warning("Cannot watch job %s (container %s): %s", job_id, container_id[:12], exc)
        return
    except Exception:
        logger.exception("Unexpected error watching job %s", job_id)
        return
    finally:
        with _watched_lock:
            _watched_jobs.discard(job_id)
    record_job_exit(job_id, exit_code)


def start_job_watcher(job_id: int, container_id: str, auto_remove: bool) -> None:
    """Start a daemon thread that records the job's outcome when its container stops."""
    with _watched_lock:
        if job_id in _watched_jobs:
            return
        _watched_jobs.add(job_id)
    threading.Thread(
        target=_watch_job,
        args=(job_id, container_id, auto_remove),
        name=f"job-watcher-{job_id}",
        daemon=True,
    ).start()


def _auto_remove_flag(flags_json: str | None) -> bool:
    try:
        return json.loads(flags_json or "{}").get("auto_remove") is True
    except (json.JSONDecodeError, AttributeError):
        return False


def reconcile_running_jobs() -> None:
    """Re-attach watchers to jobs left "running" by a previous app process."""
    db = SessionLocal()
    try:
        pending = [
            (job.id, job.container_id, _auto_remove_flag(job.flags_json))
            for job in db.query(JobRun).filter(JobRun.status == "running").all()
        ]
    finally:
        db.close()

    for job_id, container_id, auto_remove in pending:
        if not container_id:
            # The container never started; nothing will ever finish this job.
            record_job_exit(job_id, None)
            continue
        start_job_watcher(job_id, container_id, auto_remove)
    if pending:
        logger.info("Reconciling %d job(s) left running by a previous process", len(pending))
