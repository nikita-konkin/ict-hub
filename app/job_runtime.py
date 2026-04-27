"""Durable job-event runtime for SSE replay and detached container reconciliation."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from contextlib import suppress
from datetime import datetime, timezone
from xml.sax.saxutils import escape as xml_escape

from sqlalchemy.orm import Session

from app import config as cfg
from app.database import SessionLocal
from app.models import JobEvent, JobRun
from app.registry import get_converter
from app.runner import get_container_state, stream_logs

logger = logging.getLogger(__name__)

_producer_tasks: dict[int, asyncio.Task[None]] = {}
_monitor_task: asyncio.Task[None] | None = None


_ANSI_ESCAPE_RE = re.compile(r'\x1B\[[0-?]*[ -/]*[@-~]')
_INVALID_XML_CHAR_RE = re.compile(r'[\x00-\x08\x0B\x0C\x0E-\x1F]')


def _sanitize_xml_text(value: str) -> str:
    """Remove ANSI/control characters and XML-invalid codepoints."""
    text = str(value)
    text = _ANSI_ESCAPE_RE.sub("", text)
    text = _INVALID_XML_CHAR_RE.sub("", text)
    return xml_escape(text)


def xml_payload(root_name: str, **fields: str | int) -> str:
    """Build a compact XML payload for SSE consumers."""
    parts = [f"<{root_name}>"]
    for key, value in fields.items():
        parts.append(f"<{key}>{_sanitize_xml_text(value)}</{key}>")
    parts.append(f"</{root_name}>")
    return "".join(parts)


def sse_event(event_name: str, payload: str, event_id: int | None = None) -> str:
    """Encode one SSE event with optional id and multi-line payload support."""
    text = str(payload).replace("\r\n", "\n").replace("\r", "\n")
    data_lines = text.split("\n")
    encoded = ""
    if event_id is not None:
        encoded += f"id: {event_id}\n"
    encoded += f"event: {event_name}\n"
    for line in data_lines:
        encoded += f"data: {line}\n"
    return encoded + "\n"


def _parse_auto_remove(flags_json: str) -> bool:
    try:
        flags = json.loads(flags_json or "{}")
    except Exception:
        return False
    raw = flags.get("auto_remove", False)
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def append_job_event(
    db: Session,
    job_id: int,
    event_type: str,
    payload_xml: str,
) -> JobEvent:
    """Persist one event row and return it with its database id."""
    event = JobEvent(job_id=job_id, event_type=event_type, payload_xml=payload_xml)
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def _prune_job_log_events(db: Session, job_id: int, keep_last: int) -> None:
    """Keep only the last N persisted log events for a job (best-effort)."""
    if keep_last <= 0:
        db.query(JobEvent).filter(
            JobEvent.job_id == job_id,
            JobEvent.event_type == "log",
        ).delete(synchronize_session=False)
        db.commit()
        return

    cutoff_row = (
        db.query(JobEvent.id)
        .filter(JobEvent.job_id == job_id, JobEvent.event_type == "log")
        .order_by(JobEvent.id.desc())
        .offset(keep_last)
        .limit(1)
        .first()
    )
    if not cutoff_row:
        return
    cutoff_id = int(cutoff_row[0])
    db.query(JobEvent).filter(
        JobEvent.job_id == job_id,
        JobEvent.event_type == "log",
        JobEvent.id <= cutoff_id,
    ).delete(synchronize_session=False)
    db.commit()


def persist_job_finished(db: Session, job: JobRun, exit_code: int) -> JobEvent | None:
    """Mark a job terminal and emit its durable `done` event exactly once."""
    final_status = "success" if exit_code == 0 else "failed"
    job.finished_at = job.finished_at or datetime.now(timezone.utc)
    job.exit_code = exit_code
    job.status = final_status

    existing_done = (
        db.query(JobEvent)
        .filter(JobEvent.job_id == job.id, JobEvent.event_type == "done")
        .order_by(JobEvent.id.desc())
        .first()
    )
    if existing_done is not None:
        db.commit()
        return existing_done

    event = JobEvent(
        job_id=job.id,
        event_type="done",
        payload_xml=xml_payload(
            "done",
            status=final_status,
            exit_code=exit_code,
            finished_at=job.finished_at.isoformat(),
        ),
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def reconcile_job_state(job_id: int, db: Session | None = None) -> bool:
    """
    Inspect the Docker container and close out a running job when the container
    is already terminal, even if no SSE consumer is attached.
    """
    owns_session = db is None
    if owns_session:
        db = SessionLocal()

    try:
        assert db is not None
        job = db.query(JobRun).filter(JobRun.id == job_id).first()
        if not job or job.status != "running" or not job.container_id:
            return False

        state = get_container_state(job.container_id)
        # If Docker is temporarily unavailable, do not incorrectly close out a
        # running job (this commonly happens during app restarts/rebuilds).
        if str(state.get("status", "unknown")) == "unknown":
            return False
        if state.get("running"):
            return False

        exit_code = state.get("exit_code")
        if not isinstance(exit_code, int):
            exit_code = -1

        persist_job_finished(db, job, exit_code)
        return True
    finally:
        if owns_session and db is not None:
            db.close()


async def ensure_job_producer(job_id: int) -> None:
    """Ensure there is at most one active background producer per running job."""
    if not cfg.JOB_RUNTIME_ENABLED:
        return

    task = _producer_tasks.get(job_id)
    if task is not None and not task.done():
        return

    new_task = asyncio.create_task(_produce_job_events(job_id), name=f"job-producer-{job_id}")
    _producer_tasks[job_id] = new_task

    def _cleanup_task(done_task: asyncio.Task[None]) -> None:
        current = _producer_tasks.get(job_id)
        if current is done_task:
            _producer_tasks.pop(job_id, None)
        with suppress(asyncio.CancelledError):
            exc = done_task.exception()
            if exc is not None:
                logger.exception("Job producer %s crashed", job_id, exc_info=exc)

    new_task.add_done_callback(_cleanup_task)


async def _produce_job_events(job_id: int) -> None:
    """Tail container logs once and persist a durable event stream for one job."""
    db = SessionLocal()
    try:
        job = db.query(JobRun).filter(JobRun.id == job_id).first()
        if not job or job.status != "running" or not job.container_id:
            return

        if reconcile_job_state(job_id, db=db):
            return

        conv = get_converter(job.converter)
        progress_patterns = conv.get("progress_patterns", []) if conv else []
        auto_remove = _parse_auto_remove(job.flags_json)
        existing_event_count = db.query(JobEvent).filter(JobEvent.job_id == job_id).count()
        tail: str | int = "all" if existing_event_count == 0 else 0
        log_since_prune = 0

        async for event_type, payload in stream_logs(
            job.container_id,
            progress_patterns,
            log_emit_interval_sec=0.0,
            auto_remove=auto_remove,
            tail=tail,
        ):
            db.expire_all()
            job = db.query(JobRun).filter(JobRun.id == job_id).first()
            if not job or job.status != "running":
                break

            if event_type == "heartbeat":
                continue
            if event_type == "log":
                if cfg.JOB_EVENT_LOG_MAX_LINES == 0:
                    continue
                append_job_event(
                    db,
                    job_id,
                    "log",
                    xml_payload("log", message=str(payload), level="info"),
                )
                log_since_prune += 1
                if log_since_prune >= 100:
                    log_since_prune = 0
                    with suppress(Exception):
                        _prune_job_log_events(db, job_id, int(cfg.JOB_EVENT_LOG_MAX_LINES))
            elif event_type == "progress":
                append_job_event(
                    db,
                    job_id,
                    "progress",
                    xml_payload("progress", value=int(payload)),
                )
            elif event_type == "error":
                append_job_event(
                    db,
                    job_id,
                    "job_error",
                    xml_payload("job_error", message=str(payload)),
                )
                append_job_event(
                    db,
                    job_id,
                    "log",
                    xml_payload("log", message=str(payload), level="error"),
                )
                break
            elif event_type == "done":
                persist_job_finished(db, job, int(payload))
                with suppress(Exception):
                    _prune_job_log_events(db, job_id, int(cfg.JOB_EVENT_LOG_MAX_LINES))
                return

        reconcile_job_state(job_id, db=db)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Unexpected error while producing job events for job %s", job_id)
    finally:
        db.close()


async def _monitor_running_jobs() -> None:
    """Periodic safety net for detached completion and producer restarts."""
    while True:
        try:
            db = SessionLocal()
            try:
                running_job_ids = [
                    row[0]
                    for row in db.query(JobRun.id).filter(JobRun.status == "running").all()
                ]
            finally:
                db.close()

            for job_id in running_job_ids:
                try:
                    finished = reconcile_job_state(job_id)
                    if not finished:
                        await ensure_job_producer(job_id)
                except Exception:
                    logger.exception("Failed to monitor running job %s", job_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Background job monitor iteration failed")

        await asyncio.sleep(max(0.25, float(cfg.JOB_MONITOR_INTERVAL_SEC)))


async def start_job_runtime() -> None:
    """Start the background monitor once per process."""
    global _monitor_task
    if not cfg.JOB_RUNTIME_ENABLED:
        logger.info("Job runtime disabled by configuration")
        return
    if _monitor_task is not None and not _monitor_task.done():
        return
    _monitor_task = asyncio.create_task(_monitor_running_jobs(), name="job-runtime-monitor")


async def stop_job_runtime() -> None:
    """Stop background monitoring and active per-job producers."""
    global _monitor_task

    tasks_to_cancel: list[asyncio.Task[None]] = []
    if _monitor_task is not None:
        tasks_to_cancel.append(_monitor_task)
        _monitor_task = None
    tasks_to_cancel.extend(_producer_tasks.values())
    _producer_tasks.clear()

    for task in tasks_to_cancel:
        task.cancel()
    for task in tasks_to_cancel:
        with suppress(asyncio.CancelledError):
            await task
