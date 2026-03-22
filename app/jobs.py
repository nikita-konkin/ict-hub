"""
jobs.py — Job management router.

Routes:
  GET  /                         — dashboard (list of converters)
  GET  /run/{converter}          — converter run page with the flag form
  POST /jobs/start               — validate form, start container, return SSE panel fragment
  GET  /jobs/{id}/stream         — SSE log stream for a running/finished job
  POST /jobs/{id}/stop           — stop a running container
  GET  /history                  — audit log (admins see all, operators see own)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import docker.errors
from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session

from app import config as cfg
from app.auth import get_admin_user, get_current_user
from app.database import get_db
from app.models import JobRun, User
from app.registry import CONVERTERS, build_command, get_converter
from app.runner import parse_progress, start_container, stop_container, stream_logs
from fastapi.templating import Jinja2Templates

logger = logging.getLogger(__name__)
router = APIRouter(tags=["jobs"])
templates = Jinja2Templates(directory="app/templates")


def _is_truthy_checkbox(value: object) -> bool:
    """Interpret common HTML checkbox encodings as booleans."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"on", "true", "1", "yes"}


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Main landing page. Shows each registered converter as a card alongside
    the user's 5 most recent jobs so they have immediate context on activity.
    """
    recent_jobs = (
        db.query(JobRun)
        .filter(JobRun.user_id == current_user.id)
        .order_by(JobRun.started_at.desc())
        .limit(5)
        .all()
    )
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "current_user": current_user,
            "converters": CONVERTERS,
            "recent_jobs": recent_jobs,
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# Converter run page
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/run/{converter_name}", response_class=HTMLResponse)
async def run_page(
    request: Request,
    converter_name: str,
    job_id: int | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Render the flag form for a specific converter."""
    conv = get_converter(converter_name)
    if not conv:
        raise HTTPException(status_code=404, detail=f"Converter '{converter_name}' not found")

    # Show this user's last 3 jobs for this converter as quick context
    recent = (
        db.query(JobRun)
        .filter(JobRun.user_id == current_user.id, JobRun.converter == converter_name)
        .order_by(JobRun.started_at.desc())
        .limit(3)
        .all()
    )
    active_job = None
    active_stream_tail = "all"
    resume_mode = request.query_params.get("resume", "0") == "1"
    if job_id is not None:
        candidate = (
            db.query(JobRun)
            .filter(JobRun.id == job_id, JobRun.converter == converter_name)
            .first()
        )
        if (
            candidate
            and (current_user.is_admin or candidate.user_id == current_user.id)
        ):
            active_job = candidate
            if resume_mode and candidate.status == "running":
                # When reopening a running job from Recent runs, don't replay
                # the entire log from the beginning.
                active_stream_tail = "0"

    return templates.TemplateResponse(
        "run.html",
        {
            "request": request,
            "current_user": current_user,
            "converter_name": converter_name,
            "converter": conv,
            "recent_jobs": recent,
            "active_job": active_job,
            "active_stream_tail": active_stream_tail,
            "converters": CONVERTERS,
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# Start a job
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/jobs/start", response_class=HTMLResponse)
async def start_job(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Called by the HTMX form submission.

    1. Reads form data
    2. Validates the converter name
    3. Builds the Docker command and volume mapping
    4. Starts the container (detached)
    5. Persists the JobRun record
    6. Returns an HTML fragment containing the HTMX SSE panel

    The returned fragment is swapped into #job-output by HTMX. Once in the DOM
    the hx-ext="sse" attribute on the outer div causes HTMX to immediately open
    the SSE connection and start streaming logs into the log panel.
    """
    form = await request.form()
    is_htmx_request = request.headers.get("HX-Request") == "true"
    converter_name = str(form.get("converter_name", ""))

    conv = get_converter(converter_name)
    logger.info("User %r starting job with converter %r and form data %s", current_user.username, converter_name, dict(form))
    if not conv:
        return HTMLResponse(
            f'<div class="alert alert-danger">Unknown converter: {converter_name}</div>',
            status_code=400,
        )

    # Convert form data to a regular dict for processing
    form_dict = {k: v for k, v in form.items() if k != "converter_name"}

    # Global execution option (not part of converter CLI flags): docker --rm
    auto_remove = _is_truthy_checkbox(form.get("auto_remove", False))
    form_dict["auto_remove"] = auto_remove

    # Handle checkboxes: absent means unchecked in HTML form encoding
    for flag in conv["flags"]:
        if flag["type"] == "checkbox":
            key = flag["long"].lstrip("-").replace("-", "_")
            form_dict.setdefault(key, False)
            if form_dict[key] == "on":
                form_dict[key] = True

    try:
        command, volumes = build_command(converter_name, form_dict)
    except Exception as exc:
        logger.error("Command build error: %s", exc)
        return HTMLResponse(
            f'<div class="alert alert-danger">Failed to build command: {exc}</div>',
            status_code=400,
        )

    # Create the job record before starting the container so we always have
    # an audit trail, even if the container fails to start
    job = JobRun(
        user_id=current_user.id,
        converter=converter_name,
        flags_json=json.dumps(form_dict),
        rinex_path=form_dict.get("root", ""),
        output_path=form_dict.get("out", ""),
        status="running",
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    try:
        container_id = start_container(
            conv["image"],
            command,
            volumes,
            auto_remove=auto_remove,
        )
        job.container_id = container_id
        db.commit()
    except docker.errors.DockerException as exc:
        logger.error("Docker error starting job %s: %s", job.id, exc)
        job.status = "error"
        job.finished_at = datetime.now(timezone.utc)
        job.exit_code = -1
        db.commit()
        return HTMLResponse(
            f'<div class="alert alert-danger">Docker error: {exc}</div>',
            status_code=500,
        )

    # HTMX requests get a fragment swap; plain form posts should redirect back
    # to the converter page so the browser URL remains /run/{converter}.
    if not is_htmx_request:
        return RedirectResponse(url=f"/run/{converter_name}?job_id={job.id}", status_code=303)

    # Return the SSE monitoring panel. HTMX will swap this into #job-output.
    return templates.TemplateResponse(
        "job_panel.html",
        {
            "request": request,
            "job": job,
            "converter": conv,
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# SSE log stream
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/jobs/{job_id}/stream")
async def stream_job_logs(
    job_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Server-Sent Events endpoint. Streams container logs to the browser in
    real time, parsing progress updates and signalling job completion.

    Security: operators can only stream their own jobs; admins can stream any.
    """
    job = db.query(JobRun).filter(JobRun.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if not current_user.is_admin and job.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    if not job.container_id:
        raise HTTPException(status_code=400, detail="No container associated with this job")

    tail_param = request.query_params.get("tail", "all")
    if tail_param == "all":
        stream_tail: str | int = "all"
    else:
        try:
            parsed_tail = int(tail_param)
            stream_tail = max(0, parsed_tail)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid tail parameter")

    conv = get_converter(job.converter)
    progress_patterns = conv.get("progress_patterns", []) if conv else []
    log_emit_interval_sec = conv.get("log_emit_interval_sec", cfg.LOG_EMIT_INTERVAL_SEC) if conv else cfg.LOG_EMIT_INTERVAL_SEC
    auto_remove = False
    if job.flags_json:
        try:
            flags = json.loads(job.flags_json)
            auto_remove = _is_truthy_checkbox(flags.get("auto_remove", False))
        except Exception:
            logger.warning("Failed to parse flags_json for job %s", job_id)

    def sse_event(event_name: str, payload: str | int) -> str:
        """Encode one SSE event, supporting multi-line payloads correctly."""
        text = str(payload).replace("\r\n", "\n").replace("\r", "\n")
        data_lines = text.split("\n")
        encoded = f"event: {event_name}\n"
        for line in data_lines:
            encoded += f"data: {line}\n"
        return encoded + "\n"

    async def generate():
        """
        Async generator that bridges runner.stream_logs() → SSE wire format.

        SSE wire format for each event:
            event: <name>\ndata: <payload>\n\n

        Named events consumed by the HTMX SSE extension on the frontend:
          - log      → a line of container output (HTML-escaped)
          - progress → integer 0–100
          - done     → exit code integer
          - error    → error message string
          - heartbeat→ empty comment to keep the TCP connection alive
        """
        # Use a fresh DB session inside the generator since the request session
        # may be reused across async yield boundaries
        from app.database import SessionLocal
        gen_db = SessionLocal()

        try:
            async for event_type, payload in stream_logs(
                job.container_id,
                progress_patterns,
                log_emit_interval_sec=float(log_emit_interval_sec),
                auto_remove=auto_remove,
                tail=stream_tail,
            ):
                if event_type == "heartbeat":
                    # SSE comment — not dispatched as an event, just keeps the
                    # connection alive through proxies and load balancers
                    yield ": heartbeat\n\n"

                elif event_type == "log":
                    yield sse_event("log", f'<span class="log-line">{payload}</span>')

                elif event_type == "progress":
                    yield sse_event("progress", int(payload))

                elif event_type == "error":
                    yield sse_event("error", f'<span class="badge badge-danger">Error</span>')
                    yield sse_event("log", f'<span class="log-line log-line-error">{payload}</span>')

                elif event_type == "done":
                    exit_code = int(payload)
                    # Persist the job outcome to the database
                    db_job = gen_db.query(JobRun).filter(JobRun.id == job_id).first()
                    if db_job:
                        db_job.finished_at = datetime.now(timezone.utc)
                        db_job.exit_code = exit_code
                        db_job.status = "success" if exit_code == 0 else "failed"
                        gen_db.commit()
                    done_badge = (
                        '<span class="badge badge-success">Success</span>'
                        if exit_code == 0
                        else f'<span class="badge badge-danger">Failed ({exit_code})</span>'
                    )
                    yield sse_event("done", done_badge)
                    break  # end the generator — browser closes the SSE connection

        except Exception as exc:
            logger.exception("Unexpected error in SSE stream for job %s", job_id)
            yield sse_event("error", '<span class="badge badge-danger">Error</span>')
            yield sse_event("log", f'<span class="log-line log-line-error">Unexpected error: {exc}</span>')
        finally:
            gen_db.close()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disables Nginx buffering for SSE
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# Stop a job
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/jobs/{job_id}/stop")
async def stop_job(
    job_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Stop a running container. Returns the run page for the same converter."""
    job = db.query(JobRun).filter(JobRun.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if not current_user.is_admin and job.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    if job.container_id and job.status == "running":
        stop_container(job.container_id)
        job.status = "failed"
        job.finished_at = datetime.now(timezone.utc)
        job.exit_code = -2  # sentinel for "stopped by user"
        db.commit()

    return RedirectResponse(f"/run/{job.converter}", status_code=302)


# ─────────────────────────────────────────────────────────────────────────────
# Job history
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/history", response_class=HTMLResponse)
async def history(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    page: int = 1,
    per_page: int = 25,
):
    """
    Paginated audit log of job runs.
    Admins see all users' jobs; operators only see their own.
    """
    query = db.query(JobRun)
    if not current_user.is_admin:
        query = query.filter(JobRun.user_id == current_user.id)

    total = query.count()
    jobs = (
        query.order_by(JobRun.started_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    return templates.TemplateResponse(
        "history.html",
        {
            "request": request,
            "current_user": current_user,
            "jobs": jobs,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": max(1, (total + per_page - 1) // per_page),
            "converters": CONVERTERS,
        },
    )
