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

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone

import docker.errors
from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session

from app import config as cfg
from app.auth import get_admin_user, get_current_user, require_converter_access, require_page_access
from app.database import SessionLocal, get_db
from app.i18n import apply_lang_cookie, template_context
from app.job_runtime import (
    ensure_job_producer,
    persist_job_finished,
    reconcile_job_state,
    sse_event,
    xml_payload,
)
from app.models import JobEvent, JobRun, User
from app.registry import CONVERTERS, build_command, get_converter
from app.data_indexer_client import (
    list_parquet_output_structure_async,
    list_rinex_server_structure_async,
    list_tecsuite_output_structure_async,
)
from app.runner import list_running_containers, start_container, stop_container, stream_logs
from fastapi.templating import Jinja2Templates

logger = logging.getLogger(__name__)
router = APIRouter(tags=["jobs"])
templates = Jinja2Templates(directory="app/templates")

_TECSUITE_ROOT_SUBPATH_RE = re.compile(r"^/\d{4}_original(?:/\d{2,3})?$")
_DAT_PARQUET_ROOT_SUBPATH_RE = re.compile(r"^/\d{4}(?:/\d{1,3})?$")
_TECSUITE_ENV_ROOT_NOTE = "Configured from environment variable RINEX_DATA_PATH_HOST"
_ABSTEC_ENV_INPUT_NOTE = "Configured from environment variable TECSUITE_OUT_DAT_DATA_PATH_HOST"
_DAT_PARQUET_SOURCE_NOTES = {
    "tecsuite": "Configured from environment variable TECSUITE_OUT_DAT_DATA_PATH_HOST",
    "abstec": "Configured from environment variable ABSTEC_OUTPUT_DATA_PATH_HOST",
    "tecsuite-parquet": "Configured from environment variable PARQUET_OUTPUT_TECSUITE_DATA_PATH_HOST",
    "abstec-parquet": "Configured from environment variable PARQUET_OUTPUT_ABSTEC_DATA_PATH_HOST",
}


def _dat_parquet_profiles(direction: str) -> dict[str, dict[str, str]]:
    """Return env-backed source/destination profiles for the DAT <-> Parquet converter."""
    if direction == "parquet-to-dat":
        return {
            "tecsuite": {
                "label": "TEC-Suite parquet output",
                "src": cfg.PARQUET_OUTPUT_TECSUITE_DATA_PATH_HOST.strip(),
                "dst": cfg.TECSUITE_OUT_DAT_DATA_PATH_HOST.strip(),
                "src_env": "PARQUET_OUTPUT_TECSUITE_DATA_PATH_HOST",
                "dst_env": "TECSUITE_OUT_DAT_DATA_PATH_HOST",
                "source_note": _DAT_PARQUET_SOURCE_NOTES["tecsuite-parquet"],
            },
            "abstec": {
                "label": "AbsTEC parquet output",
                "src": cfg.PARQUET_OUTPUT_ABSTEC_DATA_PATH_HOST.strip(),
                "dst": cfg.ABSTEC_OUTPUT_DATA_PATH_HOST.strip(),
                "src_env": "PARQUET_OUTPUT_ABSTEC_DATA_PATH_HOST",
                "dst_env": "ABSTEC_OUTPUT_DATA_PATH_HOST",
                "source_note": _DAT_PARQUET_SOURCE_NOTES["abstec-parquet"],
            },
        }

    return {
        "tecsuite": {
            "label": "TEC-Suite DAT output",
            "src": cfg.TECSUITE_OUT_DAT_DATA_PATH_HOST.strip(),
            "dst": cfg.PARQUET_OUTPUT_TECSUITE_DATA_PATH_HOST.strip(),
            "src_env": "TECSUITE_OUT_DAT_DATA_PATH_HOST",
            "dst_env": "PARQUET_OUTPUT_TECSUITE_DATA_PATH_HOST",
            "source_note": _DAT_PARQUET_SOURCE_NOTES["tecsuite"],
        },
        "abstec": {
            "label": "AbsTEC output",
            "src": cfg.ABSTEC_OUTPUT_DATA_PATH_HOST.strip(),
            "dst": cfg.PARQUET_OUTPUT_ABSTEC_DATA_PATH_HOST.strip(),
            "src_env": "ABSTEC_OUTPUT_DATA_PATH_HOST",
            "dst_env": "PARQUET_OUTPUT_ABSTEC_DATA_PATH_HOST",
            "source_note": _DAT_PARQUET_SOURCE_NOTES["abstec"],
        },
    }


def _resolve_dat_parquet_paths(direction: str, profile_name: str, overwrite: bool) -> tuple[dict[str, str], str | None]:
    """Resolve source/destination host paths for DAT <-> Parquet from env-backed profiles."""
    profiles = _dat_parquet_profiles(direction)
    profile = profiles.get(profile_name)
    if profile is None:
        return {}, f"Select a valid DAT <-> Parquet source profile for direction '{direction}'."

    src_path = profile["src"]
    if not src_path:
        return {}, f"{profile['src_env']} is not configured."

    dst_path = src_path if overwrite else profile["dst"]
    if not dst_path:
        return {}, f"{profile['dst_env']} is not configured."

    return {
        "src": src_path,
        "dst": dst_path,
        "profile": profile_name,
        "source_note": profile["source_note"],
    }, None


def _dat_parquet_profile_matrix() -> dict[str, dict[str, dict[str, str]]]:
    """Return all DAT <-> Parquet profile variants for the run-page JavaScript."""
    return {
        "dat-to-parquet": _dat_parquet_profiles("dat-to-parquet"),
        "parquet-to-dat": _dat_parquet_profiles("parquet-to-dat"),
    }


def _reduce_to_year_days(dat_tree: list[dict[str, object]]) -> list[dict[str, object]]:
    """Keep only year/day values for compact UI payloads."""
    reduced: list[dict[str, object]] = []
    for year_item in dat_tree:
        year_name = str(year_item.get("year", "")).strip()
        if not year_name:
            continue
        days_raw = year_item.get("days", [])
        days: list[str] = []
        if isinstance(days_raw, list):
            for day_item in days_raw:
                if isinstance(day_item, dict):
                    day_name = str(day_item.get("day", "")).strip()
                    if day_name:
                        days.append(day_name)
        reduced.append({"year": year_name, "days": days})
    return reduced


def _join_host_path(base_path: str, suffix: str) -> str:
    """Join host path with '/YYYY[/DDD]' suffix while preserving base style."""
    clean_suffix = str(suffix or "").strip().replace("\\", "/")
    if not clean_suffix:
        return base_path
    clean_suffix = clean_suffix.strip("/")
    if not clean_suffix:
        return base_path
    return f"{base_path.rstrip('/\\')}/{clean_suffix}"


def _is_truthy_checkbox(value: object) -> bool:
    """Interpret common HTML checkbox encodings as booleans."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"on", "true", "1", "yes"}


def _apply_converter_flag_defaults(conv: dict, form_dict: dict[str, object]) -> dict[str, object]:
    """
    Ensure stored job flags are complete and reproducible.

    - Adds missing converter flags with their registry defaults
    - Normalizes checkbox to bool and number to int where possible
    - Preserves explicitly-submitted empty strings for text/select fields
    """
    resolved: dict[str, object] = dict(form_dict)
    for flag in conv.get("flags", []):
        key = str(flag.get("long", "")).lstrip("-").replace("-", "_")
        if not key:
            continue

        flag_type = flag.get("type")
        default = flag.get("default", "")
        raw_value = resolved.get(key, None)

        if flag_type == "checkbox":
            resolved[key] = _is_truthy_checkbox(raw_value)
            continue

        if flag_type == "number":
            if raw_value in (None, ""):
                if default not in (None, ""):
                    try:
                        resolved[key] = int(default)
                    except (TypeError, ValueError):
                        resolved[key] = default
                else:
                    resolved.setdefault(key, "")
                continue
            try:
                resolved[key] = int(raw_value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                resolved[key] = raw_value
            continue

        # text/select: only apply default when the key is missing entirely.
        if key not in resolved:
            resolved[key] = default

    return resolved


def _reconcile_running_jobs(
    db: Session,
    current_user: User,
    *,
    converter_name: str | None = None,
) -> None:
    """Refresh visible running jobs from Docker before rendering pages."""
    _discover_running_converter_containers(db, current_user, converter_name=converter_name)
    query = db.query(JobRun.id).filter(JobRun.status == "running")
    if not current_user.is_admin:
        query = query.filter(JobRun.user_id == current_user.id)
    if converter_name:
        query = query.filter(JobRun.converter == converter_name)

    for (job_id,) in query.all():
        try:
            reconcile_job_state(job_id, db=db)
        except Exception:
            logger.exception("Failed to reconcile job %s before page render", job_id)


def _image_match_score(container_image: str, converter_image: str) -> int:
    """
    Return a small score indicating whether a container image ref matches a converter image ref.

    We treat tag/registry variations as equivalent by comparing base names, so:
      - my.registry/tec-suite:latest matches tec-suite
      - tec-suite matches tec-suite:latest
    """
    container_raw = str(container_image or "").strip()
    converter_raw = str(converter_image or "").strip()
    if not container_raw or not converter_raw:
        return 0

    if container_raw == converter_raw:
        return 3

    def _base_no_tag(value: str) -> str:
        cleaned = value.split("@", 1)[0].strip()
        base = cleaned.rsplit("/", 1)[-1]
        return base.split(":", 1)[0].strip()

    if _base_no_tag(container_raw) == _base_no_tag(converter_raw):
        return 1

    container_base = container_raw.split("@", 1)[0].strip().rsplit("/", 1)[-1]
    converter_base = converter_raw.split("@", 1)[0].strip().rsplit("/", 1)[-1]
    if container_base == converter_base:
        return 2
    return 0


def _discover_running_converter_containers(
    db: Session,
    current_user: User,
    *,
    converter_name: str | None = None,
) -> int:
    """
    Scan Docker for currently running converter containers and import them into JobRun.

    This covers the "ict-hub reset/rebuild" case where converter containers are still running,
    but the database has no record of them.
    """
    if not cfg.DISCOVER_RUNNING_CONTAINERS:
        return 0

    if converter_name:
        if converter_name not in CONVERTERS:
            return 0
        converters = {converter_name: CONVERTERS[converter_name]}
    else:
        converters = CONVERTERS

    try:
        running = list_running_containers()
    except Exception:
        logger.exception("Failed to scan running Docker containers")
        return 0

    imported = 0
    for row in running:
        container_id = str(row.get("id", "") or "").strip()
        if not container_id:
            continue

        existing = (
            db.query(JobRun.id)
            .filter(JobRun.container_id == container_id)
            .order_by(JobRun.id.desc())
            .first()
        )
        if existing is not None:
            continue

        labels = row.get("labels") or {}
        if not isinstance(labels, dict):
            labels = {}

        label_user_id_raw = str(labels.get("ict-hub.user_id", "") or "").strip()
        label_converter = str(labels.get("ict-hub.converter", "") or "").strip()

        if not current_user.is_admin:
            # Operators only "re-attach" containers that were started by ict-hub
            # and explicitly labeled for that user.
            if not label_user_id_raw or not label_converter:
                continue
            if label_user_id_raw != str(current_user.id):
                continue
            if label_converter not in converters:
                continue

        owner_user_id = current_user.id
        if label_user_id_raw:
            try:
                label_user_id = int(label_user_id_raw)
            except ValueError:
                label_user_id = None
            if label_user_id is not None:
                exists = db.query(User.id).filter(User.id == label_user_id).first()
                if exists is not None:
                    owner_user_id = label_user_id

        matched_converter: str | None = None
        if label_converter and label_converter in converters:
            matched_converter = label_converter
        else:
            container_image = str(row.get("image", "") or "").strip()
            best_score = 0
            for conv_key, conv in converters.items():
                score = _image_match_score(container_image, str(conv.get("image", "") or ""))
                if score > best_score:
                    best_score = score
                    matched_converter = conv_key
                    if score >= 3:
                        break

        if not matched_converter:
            continue

        started_at = row.get("started_at")
        if not isinstance(started_at, datetime):
            started_at = datetime.now(timezone.utc)

        flags = {
            "discovered": True,
            "source": "docker_scan",
            "docker": {
                "image": str(row.get("image", "") or ""),
                "name": str(row.get("name", "") or ""),
                "labels": labels,
            },
        }
        job = JobRun(
            user_id=owner_user_id,
            converter=matched_converter,
            status="running",
            container_id=container_id,
            started_at=started_at,
            flags_json=json.dumps(flags, ensure_ascii=False),
        )
        db.add(job)
        imported += 1

    if imported:
        db.commit()
    return imported


def _job_auto_remove_enabled(job: JobRun) -> bool:
    """Read the persisted auto-remove flag from a job record."""
    try:
        flags = json.loads(job.flags_json or "{}")
    except Exception:
        return False
    return _is_truthy_checkbox(flags.get("auto_remove", False))


async def _stream_job_logs_direct(
    job: JobRun,
    db: Session,
    *,
    tail: str | int = "all",
) -> asyncio.AsyncGenerator[str, None]:
    """
    Fallback path for live log delivery when durable job events are not being
    produced yet. This keeps the UI usable even if the background runtime is
    unhealthy for a specific job.
    """
    conv = get_converter(job.converter)
    progress_patterns = conv.get("progress_patterns", []) if conv else []
    auto_remove = _job_auto_remove_enabled(job)

    async for event_type, payload in stream_logs(
        job.container_id,
        progress_patterns,
        log_emit_interval_sec=0.0,
        auto_remove=auto_remove,
        tail=tail,
    ):
        if event_type == "heartbeat":
            yield ": heartbeat\n\n"
            continue

        if event_type == "log":
            yield sse_event(
                "log",
                xml_payload("log", message=str(payload), level="info"),
            )
            continue

        if event_type == "progress":
            yield sse_event(
                "progress",
                xml_payload("progress", value=int(payload)),
            )
            continue

        if event_type == "error":
            message = str(payload)
            yield sse_event(
                "job_error",
                xml_payload("job_error", message=message),
            )
            break

        if event_type == "done":
            db.expire_all()
            db_job = db.query(JobRun).filter(JobRun.id == job.id).first()
            if db_job is not None:
                persist_job_finished(db, db_job, int(payload))
            yield sse_event(
                "done",
                xml_payload(
                    "done",
                    status="success" if int(payload) == 0 else "failed",
                    exit_code=int(payload),
                ),
            )
            return


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_page_access("dashboard")),
):
    """
    Main landing page. Shows each registered converter as a card alongside
    the user's 5 most recent jobs so they have immediate context on activity.
    """
    _reconcile_running_jobs(db, current_user)
    recent_jobs = (
        db.query(JobRun)
        .filter(JobRun.user_id == current_user.id)
        .order_by(JobRun.started_at.desc())
        .limit(5)
        .all()
    )
    response = templates.TemplateResponse(
        "dashboard.html",
        template_context(
            request,
            current_user=current_user,
            converters=CONVERTERS,
            recent_jobs=recent_jobs,
        ),
    )
    return apply_lang_cookie(request, response)


# ─────────────────────────────────────────────────────────────────────────────
# Converter run page
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/run/{converter_name}", response_class=HTMLResponse)
async def run_page(
    request: Request,
    converter_name: str,
    job_id: int | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_converter_access()),
):
    """Render the flag form for a specific converter."""
    # Reconciliation can immediately close out very short-lived containers, which
    # breaks the non-HTMX redirect flow (the user lands on /run/... and expects to
    # see the live panel). Only reconcile when no explicit job_id is requested.
    if job_id is None:
        _reconcile_running_jobs(db, current_user, converter_name=converter_name)
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
    active_stream_tail: str | int = int(cfg.LOG_PAGELOAD_TAIL_LINES)
    tec_rinex_tree: list[dict[str, object]] = []
    tec_rinex_host_path = ""
    abstec_dat_tree: list[dict[str, object]] = []
    dat_parquet_profiles = _dat_parquet_profiles("dat-to-parquet")
    dat_parquet_profile_matrix = _dat_parquet_profile_matrix()
    dat_parquet_source_tree_matrix: dict[str, dict[str, list[dict[str, object]]]] = {
        "dat-to-parquet": {"tecsuite": [], "abstec": []},
        "parquet-to-dat": {"tecsuite": [], "abstec": []},
    }
    if job_id is not None:
        candidate = (
            db.query(JobRun)
            .filter(JobRun.id == job_id, JobRun.converter == converter_name)
            .first()
        )
        if (
            candidate
            and (current_user.is_admin or candidate.user_id == current_user.id)
            and candidate.status == "running"
            and bool(candidate.container_id)
        ):
            active_job = candidate
    else:
        running_query = (
            db.query(JobRun)
            .filter(
                JobRun.converter == converter_name,
                JobRun.status == "running",
                JobRun.container_id.isnot(None),
            )
            .order_by(JobRun.started_at.desc())
        )
        if not current_user.is_admin:
            running_query = running_query.filter(JobRun.user_id == current_user.id)
        active_job = running_query.first()

    if converter_name == "tec-suite":
        tec_rinex_host_path = cfg.RINEX_DATA_PATH_HOST
        scan_path = cfg.RINEX_DATA_PATH_CONTAINER or tec_rinex_host_path
        tec_rinex_tree = await list_rinex_server_structure_async(scan_path) if scan_path else []
    elif converter_name == "abstec-suite":
        abstec_scan_path = cfg.TECSUITE_OUT_DAT_DATA_PATH_CONTAINER.strip()
        host_path = cfg.TECSUITE_OUT_DAT_DATA_PATH_HOST.strip()
        if not abstec_scan_path:
            abstec_scan_path = host_path
        abstec_dat_tree = await list_tecsuite_output_structure_async(abstec_scan_path) if abstec_scan_path else []
    elif converter_name == "dat-parquet-handler":
        default_direction = "dat-to-parquet"
        dat_parquet_profiles = _dat_parquet_profiles(default_direction)
        dat_parquet_profile_matrix = _dat_parquet_profile_matrix()

        tecsuite_scan_path = cfg.TECSUITE_OUT_DAT_DATA_PATH_CONTAINER.strip()
        tecsuite_host_path = cfg.TECSUITE_OUT_DAT_DATA_PATH_HOST.strip()
        if not tecsuite_scan_path:
            tecsuite_scan_path = tecsuite_host_path

        abstec_container_path = cfg.ABSTEC_OUTPUT_DATA_PATH_CONTAINER.strip()
        abstec_host_path = cfg.ABSTEC_OUTPUT_DATA_PATH_HOST.strip()
        abstec_scan_path = abstec_container_path or abstec_host_path

        # parquet-to-dat sources: parquet output directories
        parquet_tecsuite_container = cfg.PARQUET_OUTPUT_TECSUITE_DATA_PATH_CONTAINER.strip()
        parquet_tecsuite_host = cfg.PARQUET_OUTPUT_TECSUITE_DATA_PATH_HOST.strip()
        parquet_tecsuite_scan = parquet_tecsuite_container or parquet_tecsuite_host

        parquet_abstec_container = cfg.PARQUET_OUTPUT_ABSTEC_DATA_PATH_CONTAINER.strip()
        parquet_abstec_host = cfg.PARQUET_OUTPUT_ABSTEC_DATA_PATH_HOST.strip()
        parquet_abstec_scan = parquet_abstec_container or parquet_abstec_host
        tecsuite_tree, abstec_tree, parquet_tecsuite_tree, parquet_abstec_tree = await asyncio.gather(
            list_tecsuite_output_structure_async(tecsuite_scan_path) if tecsuite_scan_path else asyncio.sleep(0, result=[]),
            list_parquet_output_structure_async(abstec_scan_path) if abstec_scan_path else asyncio.sleep(0, result=[]),
            list_parquet_output_structure_async(parquet_tecsuite_scan) if parquet_tecsuite_scan else asyncio.sleep(0, result=[]),
            list_parquet_output_structure_async(parquet_abstec_scan) if parquet_abstec_scan else asyncio.sleep(0, result=[]),
        )

        dat_parquet_source_tree_matrix = {
            "dat-to-parquet": {
                "tecsuite": _reduce_to_year_days(tecsuite_tree),
                "abstec": abstec_tree,  # already in {year, days: [str]} format
            },
            "parquet-to-dat": {
                "tecsuite": parquet_tecsuite_tree,
                "abstec": parquet_abstec_tree,
            },
        }

    response = templates.TemplateResponse(
        "run.html",
        template_context(
            request,
            current_user=current_user,
            converter_name=converter_name,
            converter=conv,
            recent_jobs=recent,
            active_job=active_job,
            active_stream_tail=active_stream_tail,
            tec_rinex_host_path=tec_rinex_host_path,
            tec_rinex_tree=tec_rinex_tree,
            tec_rinex_scan_path=cfg.RINEX_DATA_PATH_CONTAINER or tec_rinex_host_path,
            abstec_dat_tree=abstec_dat_tree,
            abstec_dat_host_path=cfg.TECSUITE_OUT_DAT_DATA_PATH_HOST,
            abstec_dat_scan_path=(
                cfg.TECSUITE_OUT_DAT_DATA_PATH_CONTAINER
                or cfg.TECSUITE_OUT_DAT_DATA_PATH_HOST
            ),
            abstec_output_host_path=cfg.ABSTEC_OUTPUT_DATA_PATH_HOST,
            dat_parquet_profiles=dat_parquet_profiles,
            dat_parquet_profile_matrix=dat_parquet_profile_matrix,
            dat_parquet_source_tree_matrix=dat_parquet_source_tree_matrix,
            dat_parquet_default_direction="dat-to-parquet",
            converters=CONVERTERS,
        ),
    )
    return apply_lang_cookie(request, response)


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

    if not current_user.can_access_converter(converter_name):
        if is_htmx_request:
            return HTMLResponse(
                '<div class="alert alert-danger">Access denied for this converter.</div>',
                status_code=403,
            )
        raise HTTPException(status_code=403, detail="Access denied")

    # Convert form data to a regular dict for processing
    form_dict = {k: v for k, v in form.items() if k != "converter_name"}
    dat_parquet_source_note = ""

    if converter_name == "tec-suite":
        root_host = cfg.RINEX_DATA_PATH_HOST.strip()
        root_subpath = str(form_dict.get("root_subpath", "")).strip()
        if not root_host:
            return HTMLResponse(
                '<div class="alert alert-danger">RINEX_DATA_PATH_HOST is not configured.</div>',
                status_code=400,
            )
        if not _TECSUITE_ROOT_SUBPATH_RE.fullmatch(root_subpath):
            return HTMLResponse(
                '<div class="alert alert-danger">Select a valid year/day folder before running TEC-Suite.</div>',
                status_code=400,
            )
        form_dict["root"] = root_host
    elif converter_name == "abstec-suite":
        dat_root_host = cfg.TECSUITE_OUT_DAT_DATA_PATH_HOST.strip()
        if not dat_root_host:
            return HTMLResponse(
                '<div class="alert alert-danger">TECSUITE_OUT_DAT_DATA_PATH_HOST is not configured.</div>',
                status_code=400,
            )
        form_dict["dat_path"] = dat_root_host
        form_dict["output_dir"] = cfg.ABSTEC_OUTPUT_DATA_PATH_HOST.strip()
    elif converter_name == "dat-parquet-handler":
        direction = str(form_dict.get("direction", "dat-to-parquet")).strip() or "dat-to-parquet"
        profile_name = str(form.get("dataset_profile", "tecsuite")).strip() or "tecsuite"
        overwrite = _is_truthy_checkbox(form.get("overwrite", False))
        root_subpath = str(form.get("root_subpath", "")).strip()
        day_from_raw = str(form.get("day_from", "")).strip()
        day_to_raw = str(form.get("day_to", "")).strip()
        resolved_paths, error_message = _resolve_dat_parquet_paths(direction, profile_name, overwrite)
        if error_message:
            return HTMLResponse(
                f'<div class="alert alert-danger">{error_message}</div>',
                status_code=400,
            )

        day_from = None
        day_to = None
        if day_from_raw:
            if not day_from_raw.isdigit():
                return HTMLResponse(
                    '<div class="alert alert-danger">--day-from must be an integer in range 1..366.</div>',
                    status_code=400,
                )
            day_from = int(day_from_raw)
            if day_from < 1 or day_from > 366:
                return HTMLResponse(
                    '<div class="alert alert-danger">--day-from must be in range 1..366.</div>',
                    status_code=400,
                )

        if day_to_raw:
            if not day_to_raw.isdigit():
                return HTMLResponse(
                    '<div class="alert alert-danger">--day-to must be an integer in range 1..366.</div>',
                    status_code=400,
                )
            day_to = int(day_to_raw)
            if day_to < 1 or day_to > 366:
                return HTMLResponse(
                    '<div class="alert alert-danger">--day-to must be in range 1..366.</div>',
                    status_code=400,
                )

        if day_from is not None and day_to is not None and day_from > day_to:
            return HTMLResponse(
                '<div class="alert alert-danger">--day-from must be less than or equal to --day-to.</div>',
                status_code=400,
            )

        if day_from is not None:
            form_dict["day_from"] = day_from
        if day_to is not None:
            form_dict["day_to"] = day_to

        if root_subpath:
            if not _DAT_PARQUET_ROOT_SUBPATH_RE.fullmatch(root_subpath):
                return HTMLResponse(
                    '<div class="alert alert-danger">Select a valid year/day folder before running DAT <-> Parquet.</div>',
                    status_code=400,
                )
            form_dict["src"] = _join_host_path(resolved_paths["src"], root_subpath)
            form_dict["dst"] = _join_host_path(resolved_paths["dst"], root_subpath)
        else:
            form_dict["src"] = resolved_paths["src"]
            form_dict["dst"] = resolved_paths["dst"]
        form_dict["dataset_profile"] = resolved_paths["profile"]
        form_dict["root_subpath"] = root_subpath
        dat_parquet_source_note = resolved_paths["source_note"]

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

    # Persist effective flags (include defaults) so history is fully reproducible.
    form_dict = _apply_converter_flag_defaults(conv, form_dict)

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
        flags_json=json.dumps(form_dict, ensure_ascii=False),
        rinex_path=(
            _TECSUITE_ENV_ROOT_NOTE
            if converter_name == "tec-suite"
            else (
                _ABSTEC_ENV_INPUT_NOTE
                if converter_name == "abstec-suite"
                else (
                    dat_parquet_source_note
                    if converter_name == "dat-parquet-handler"
                    else form_dict.get("root", "")
                )
            )
        ),
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
            labels={
                "ict-hub.converter": str(converter_name),
                "ict-hub.job_id": str(job.id),
                "ict-hub.user_id": str(current_user.id),
            },
        )
        job.container_id = container_id
        db.commit()
        try:
            await ensure_job_producer(job.id)
        except Exception:
            logger.exception("Failed to bootstrap job producer for job %s", job.id)
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
    response = templates.TemplateResponse(
        "job_panel.html",
        template_context(request, job=job, converter=conv),
    )
    return apply_lang_cookie(request, response)


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

    after_event_id_raw = request.query_params.get("after_event_id", "").strip()
    if not after_event_id_raw:
        after_event_id_raw = request.headers.get("Last-Event-ID", "").strip()
    after_event_id: int | None = None
    if after_event_id_raw:
        try:
            after_event_id = max(0, int(after_event_id_raw))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid after_event_id")

    if job.status == "running":
        await ensure_job_producer(job_id)

    def _initial_cursor(gen_db: Session) -> int:
        if after_event_id is not None:
            return after_event_id

        max_existing_id = (
            gen_db.query(JobEvent.id)
            .filter(JobEvent.job_id == job_id)
            .order_by(JobEvent.id.desc())
            .limit(1)
            .scalar()
        )
        if max_existing_id is None:
            return 0
        if stream_tail == "all":
            return 0
        if isinstance(stream_tail, int) and stream_tail <= 0:
            return int(max_existing_id)
        if isinstance(stream_tail, int):
            recent_ids = [
                row[0]
                for row in (
                    gen_db.query(JobEvent.id)
                    .filter(JobEvent.job_id == job_id)
                    .order_by(JobEvent.id.desc())
                    .limit(stream_tail)
                    .all()
                )
            ]
            if recent_ids:
                return max(0, min(recent_ids) - 1)
        return 0
    async def generate():
        """
        Replay durable events from the database and then poll for newly
        persisted events. Producers write to JobEvent independently of any
        active SSE client, so reconnects can resume via Last-Event-ID.
        """
        gen_db = SessionLocal()
        last_sent_id = _initial_cursor(gen_db)
        heartbeat_at = time.monotonic()
        first_event_deadline = time.monotonic() + max(0.25, float(cfg.JOB_EVENT_BOOTSTRAP_TIMEOUT_SEC))
        saw_durable_event = False

        try:
            while True:
                gen_db.expire_all()
                pending_events = (
                    gen_db.query(JobEvent)
                    .filter(JobEvent.job_id == job_id, JobEvent.id > last_sent_id)
                    .order_by(JobEvent.id.asc())
                    .limit(200)
                    .all()
                )

                if pending_events:
                    saw_durable_event = True
                    for event in pending_events:
                        yield sse_event(event.event_type, event.payload_xml, event_id=event.id)
                        last_sent_id = event.id
                        heartbeat_at = time.monotonic()
                        if event.event_type == "done":
                            return
                    continue

                db_job = gen_db.query(JobRun).filter(JobRun.id == job_id).first()
                if db_job is None:
                    return

                if db_job.status == "running":
                    await ensure_job_producer(job_id)
                    if (
                        not saw_durable_event
                        and last_sent_id == 0
                        and time.monotonic() >= first_event_deadline
                    ):
                        logger.warning(
                            "Falling back to direct log streaming for job %s after %.2fs without durable events",
                            job_id,
                            float(cfg.JOB_EVENT_BOOTSTRAP_TIMEOUT_SEC),
                        )
                        async for chunk in _stream_job_logs_direct(db_job, gen_db, tail=stream_tail):
                            yield chunk
                        return
                else:
                    if db_job.exit_code is not None:
                        persist_job_finished(gen_db, db_job, int(db_job.exit_code))
                        continue
                    return

                if (time.monotonic() - heartbeat_at) >= float(cfg.SSE_HEARTBEAT_INTERVAL):
                    yield ": heartbeat\n\n"
                    heartbeat_at = time.monotonic()

                await asyncio.sleep(max(0.1, float(cfg.JOB_EVENT_POLL_INTERVAL_SEC)))

        except Exception as exc:
            logger.exception("Unexpected error in SSE stream for job %s", job_id)
            yield sse_event(
                "job_error",
                f"<job_error><message>Unexpected stream error: {exc}</message></job_error>",
            )
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

@router.get("/jobs/{job_id}/open")
async def open_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Redirect to the correct /run/{converter} page for a given job id."""
    job = db.query(JobRun).filter(JobRun.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if not current_user.is_admin and job.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    if not current_user.can_access_converter(job.converter):
        raise HTTPException(status_code=403, detail="Access denied")

    return RedirectResponse(url=f"/run/{job.converter}?job_id={job.id}", status_code=303)


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

    if not current_user.can_access_converter(job.converter):
        raise HTTPException(status_code=403, detail="Access denied")

    if job.container_id and job.status == "running":
        stop_container(job.container_id)
        persist_job_finished(db, job, -2)  # sentinel for "stopped by user"

    return RedirectResponse(f"/run/{job.converter}", status_code=302)


# ─────────────────────────────────────────────────────────────────────────────
# Job history
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/history", response_class=HTMLResponse)
async def history(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_page_access("history")),
    page: int = 1,
    per_page: int = 25,
):
    """
    Paginated audit log of job runs.
    Admins see all users' jobs; operators only see their own.
    """
    _reconcile_running_jobs(db, current_user)
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
    response = templates.TemplateResponse(
        "history.html",
        template_context(
            request,
            current_user=current_user,
            jobs=jobs,
            total=total,
            page=page,
            per_page=per_page,
            total_pages=max(1, (total + per_page - 1) // per_page),
            converters=CONVERTERS,
        ),
    )
    return apply_lang_cookie(request, response)
