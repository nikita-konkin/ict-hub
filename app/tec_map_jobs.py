"""
tec_map_jobs.py — TEC map animations rendered in the background.

A week of frames takes minutes, longer than browsers and proxies keep a
request open, so the IonMaps page submits an animation as a job, polls its
progress and downloads the file when it is ready.

Animations render one at a time (`render_slot`), each with up to
TEC_MAP_WORKERS processes, which caps the CPU they use together; further jobs
wait in a queue. Jobs live in memory, so a restart forgets them, and finished
files are deleted after TEC_MAP_RENDER_TTL_SEC.
"""

from __future__ import annotations

import logging
import queue
import shutil
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app import config as cfg
from app.tec_map_parallel import Progress

logger = logging.getLogger(__name__)

MAX_ACTIVE_JOBS_PER_USER = 3

# Held by whichever animation is rendering, background job or direct request.
render_slot = threading.Lock()


class JobLimitError(RuntimeError):
    pass


@dataclass(frozen=True)
class RenderResult:
    content: bytes
    media_type: str
    filename: str


@dataclass
class AnimationJob:
    id: str
    user_id: int
    key: str  # identifies the request, so a repeat reuses the job
    work: Callable[[Progress], RenderResult] | None = field(repr=False)
    status: str = "queued"  # queued | running | done | failed
    stage: str = "queued"  # queued | loading | gridding | drawing | encoding | done | failed
    done: int = 0
    total: int = 0
    error: str = ""
    media_type: str = ""
    filename: str = ""
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    @property
    def path(self) -> Path:
        return Path(cfg.TEC_MAP_RENDER_DIR) / self.id


_jobs: dict[str, AnimationJob] = {}
_lock = threading.Lock()
_queue: queue.Queue[str] = queue.Queue()
_worker: threading.Thread | None = None


def submit(user_id: int, key: str, work: Callable[[Progress], RenderResult]) -> AnimationJob:
    """Queue `work`, or return the user's job for the same request if it is queued, running or done."""
    with _lock:
        _expire_locked()
        for job in _jobs.values():
            if job.user_id == user_id and job.key == key and job.status != "failed":
                return job
        active = sum(1 for job in _jobs.values() if job.user_id == user_id and job.status in ("queued", "running"))
        if active >= MAX_ACTIVE_JOBS_PER_USER:
            raise JobLimitError(f"You already have {active} animations queued or rendering; wait for one to finish.")
        job = AnimationJob(id=uuid.uuid4().hex, user_id=user_id, key=key, work=work)
        _jobs[job.id] = job
        _ensure_worker_locked()
    _queue.put(job.id)
    return job


def get(job_id: str) -> AnimationJob | None:
    with _lock:
        _expire_locked()
        return _jobs.get(job_id)


def describe(job: AnimationJob) -> dict[str, Any]:
    with _lock:
        ahead = sum(1 for other in _jobs.values() if other.status == "queued" and other.created_at < job.created_at)
        running = any(other.status == "running" for other in _jobs.values())
        return {
            "job_id": job.id,
            "status": job.status,
            "stage": job.stage,
            "done": job.done,
            "total": job.total,
            "queue_position": ahead + int(running) if job.status == "queued" else 0,
            "error": job.error or None,
            "status_url": f"/tec-map/gif/jobs/{job.id}",
            "result_url": f"/tec-map/gif/jobs/{job.id}/result" if job.status == "done" else None,
        }


def error_message(exc: BaseException) -> str:
    """A message for the user; ValueError and FileNotFoundError already explain the request's problem."""
    if isinstance(exc, (ValueError, FileNotFoundError)):
        return str(exc)
    if isinstance(exc, BrokenProcessPool):
        return (
            "A rendering process stopped unexpectedly, most likely out of memory. "
            "Try fewer days or stations, or ask the administrator to lower TEC_MAP_LOAD_WORKERS."
        )
    return f"TEC map animation rendering failed: {exc}"


def _ensure_worker_locked() -> None:
    global _worker
    if _worker is not None and _worker.is_alive():
        return
    # Files from before this process started belong to jobs it never knew.
    render_dir = Path(cfg.TEC_MAP_RENDER_DIR)
    if render_dir.is_dir():
        for path in render_dir.iterdir():
            if path.name not in _jobs:
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)
    _worker = threading.Thread(target=_work_loop, name="tec-map-jobs", daemon=True)
    _worker.start()


def _expire_locked() -> None:
    cutoff = time.time() - cfg.TEC_MAP_RENDER_TTL_SEC
    for job in list(_jobs.values()):
        if job.finished_at is not None and job.finished_at < cutoff:
            job.path.unlink(missing_ok=True)
            del _jobs[job.id]


def _work_loop() -> None:
    while True:
        job_id = _queue.get()
        with _lock:
            job = _jobs.get(job_id)
        if job is not None and job.status == "queued":
            _run(job)


def _run(job: AnimationJob) -> None:
    def progress(stage: str, done: int, total: int) -> None:
        with _lock:
            job.stage, job.done, job.total = stage, done, total

    work = job.work
    if work is None:
        return
    with render_slot:
        with _lock:
            job.status, job.stage = "running", "loading"
        started = time.monotonic()
        try:
            result = work(progress)
            job.path.parent.mkdir(parents=True, exist_ok=True)
            job.path.write_bytes(result.content)
        except Exception as exc:
            logger.exception("tec-map job %s failed", job.id)
            with _lock:
                job.status, job.stage, job.error = "failed", "failed", error_message(exc)
                job.finished_at = time.time()
        else:
            logger.info("tec-map job %s done in %.1fs", job.id, time.monotonic() - started)
            with _lock:
                job.status, job.stage = "done", "done"
                job.media_type, job.filename = result.media_type, result.filename
                job.finished_at = time.time()
        finally:
            job.work = None  # releases the request it captured
