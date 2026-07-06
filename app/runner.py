"""
runner.py — Docker execution engine.

This module is the only place in the codebase that talks to Docker. It provides:
  - start_container()  → starts a detached container, returns its ID
  - stream_logs()      → async generator yielding (event_type, data) tuples
  - parse_progress()   → extracts 0–100 progress from a single log line
  - stop_container()   → gracefully stops a running container

Design note: Docker's Python SDK is synchronous. All blocking calls are
offloaded to a thread pool via asyncio.get_event_loop().run_in_executor()
so they don't block FastAPI's event loop.
"""
from __future__ import annotations

import asyncio
import logging
import queue
import re
import threading
from datetime import datetime, timezone
from typing import AsyncGenerator

import docker
import docker.errors

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def _parse_docker_datetime(value: str | None) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    raw = raw.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(raw)
    except Exception:
        return None


def _best_container_image_ref(container: object) -> str:
    """
    Return best-effort image ref string for a Docker SDK container object.
    Prefer repository tags when available; fall back to attrs.Config.Image.
    """
    try:
        image = getattr(container, "image", None)
        tags = getattr(image, "tags", None) if image is not None else None
        if isinstance(tags, list) and tags:
            return str(tags[0])
    except Exception:
        pass
    try:
        attrs = getattr(container, "attrs", {}) or {}
        config = attrs.get("Config", {}) or {}
        img = config.get("Image")
        if img:
            return str(img)
    except Exception:
        pass
    return ""


def list_running_containers() -> list[dict[str, object]]:
    """
    Return lightweight metadata for currently running Docker containers.

    Each row includes:
      id, name, image, labels, started_at (datetime|None)
    """
    try:
        client = docker.from_env()
        containers = client.containers.list(filters={"status": "running"})
    except Exception as exc:
        logger.warning("Failed to list running containers from Docker: %s", exc)
        return []

    results: list[dict[str, object]] = []
    for container in containers:
        try:
            container.reload()
        except Exception:
            pass

        attrs = getattr(container, "attrs", {}) or {}
        state = attrs.get("State", {}) or {}
        started_at = _parse_docker_datetime(state.get("StartedAt"))
        labels = {}
        try:
            labels = dict((attrs.get("Config", {}) or {}).get("Labels", {}) or {})
        except Exception:
            labels = {}

        results.append(
            {
                "id": str(getattr(container, "id", "") or ""),
                "name": str(getattr(container, "name", "") or ""),
                "image": _best_container_image_ref(container),
                "labels": labels,
                "started_at": started_at,
            }
        )
    return results


def start_container(
    image: str,
    command: list[str],
    volumes: dict,
    auto_remove: bool = False,
    labels: dict[str, str] | None = None,
) -> str:
    """
    Start a Docker container in detached mode (non-blocking) and return its ID.

    Set auto_remove=True to run the container with Docker's --rm behavior.
    When disabled (default), containers are preserved after completion.

    Raises docker.errors.DockerException on any failure (image not found,
    daemon not reachable, etc.).
    """
    client = docker.from_env()
    logger.info(
        "Starting container: image=%s command=%s volumes=%s auto_remove=%s",
        image, command, list(volumes.keys()), auto_remove
    )
    container = client.containers.run(
        image=image,
        command=command,
        volumes=volumes,
        detach=True,
        remove=auto_remove,
        labels=labels or {},
    )
    logger.info("Container started: id=%s", container.short_id)
    return container.id


def ensure_container_running(name: str) -> str:
    """
    Ensure the named container exists and is running, starting it if needed.

    Returns:
      "running"   — already running, nothing done
      "started"   — existed but was stopped/paused; it has been (re)started
      "not_found" — no container with that name exists

    Raises docker.errors.DockerException if the daemon is unreachable or the
    start itself fails.
    """
    client = docker.from_env()
    try:
        container = client.containers.get(name)
    except docker.errors.NotFound:
        return "not_found"
    container.reload()
    state = container.attrs.get("State", {}) or {}
    if bool(state.get("Paused", False)):
        logger.info("Container %s is paused; unpausing", name)
        container.unpause()
        return "started"
    if bool(state.get("Running", False)):
        return "running"
    logger.info(
        "Container %s is %s; starting it", name, state.get("Status", "unknown")
    )
    container.start()
    return "started"


async def stream_logs(
    container_id: str,
    progress_patterns: list[str],
    log_emit_interval_sec: float = 0.0,
    auto_remove: bool = False,
    tail: str | int = "all",
) -> AsyncGenerator[tuple[str, str | int], None]:
    """
    Async generator that yields (event_type, payload) tuples by reading a
    container's log stream in a background thread.

    Yielded event types:
      ("log",      "<html-escaped log line>")   — a line of container stdout/stderr
      ("progress", <int 0–100>)                  — parsed progress percentage
      ("done",     <int exit_code>)              — container finished

    The generator waits for log completion and emits final exit status.
    Container removal is controlled by Docker's auto-remove (--rm) setting.
    """
    log_queue: queue.Queue[tuple[str, object]] = queue.Queue()

    def _read_logs() -> None:
        """Blocking thread: reads logs line-by-line and posts to the queue."""
        client = docker.from_env()
        try:
            container = client.containers.get(container_id)
            # Explicitly request both stdout and stderr. Some images/log drivers
            # may otherwise produce no output with defaults.
            for chunk in container.logs(
                stream=True,
                follow=True,
                stdout=True,
                stderr=True,
                tail=tail,
                timestamps=False,
            ):
                line = chunk.decode("utf-8", errors="replace").rstrip("\n\r")
                if line:
                    log_queue.put(("log", line))

            # Best-effort exit code capture while the container object is still
            # available (important when auto-remove is enabled).
            wait_result = container.wait()
            status_code: int | None = None
            if isinstance(wait_result, dict):
                raw_status_code = wait_result.get("StatusCode")
                if isinstance(raw_status_code, int):
                    status_code = raw_status_code
            elif isinstance(wait_result, int):
                status_code = wait_result

            if status_code is not None:
                log_queue.put(("exit_code", status_code))
        except docker.errors.NotFound:
            log_queue.put(("error", f"Container {container_id[:12]} not found"))
        except Exception as exc:
            log_queue.put(("error", str(exc)))
        finally:
            # Sentinel tells the async side the thread is done reading
            log_queue.put(("_eof", None))

    # Launch the blocking reader in a daemon thread so it doesn't prevent shutdown
    thread = threading.Thread(target=_read_logs, daemon=True)
    thread.start()

    loop = asyncio.get_event_loop()
    stream_exit_code: int | None = None
    last_progress: int | None = None

    while True:
        # get() is blocking — run it in the executor to avoid blocking the loop
        try:
            event_type, payload = await loop.run_in_executor(
                None, lambda: log_queue.get(timeout=15.0)
            )
        except queue.Empty:
            # Heartbeat: keeps the SSE connection alive during long pauses
            yield ("heartbeat", "")
            continue

        if event_type == "_eof":
            break
        elif event_type == "exit_code":
            try:
                stream_exit_code = int(payload)
            except (TypeError, ValueError):
                pass
        elif event_type == "error":
            yield ("error", str(payload))
            break
        else:
            line = str(payload)
            # Stream every log line; progress extraction is independent so
            # UI log visibility does not depend on regex matches.
            yield ("log", line)

            progress = parse_progress(line, progress_patterns)
            if progress is not None:
                # Keep progress monotonic for UI stability when multiple
                # patterns match different scales in the same log stream.
                if last_progress is None or progress > last_progress:
                    yield ("progress", progress)
                    last_progress = progress

    # Container has finished writing logs — resolve final exit code.
    if stream_exit_code is not None:
        exit_code = stream_exit_code
    elif auto_remove:
        exit_code = await loop.run_in_executor(None, _get_exit_code_only, container_id)
    else:
        exit_code = await loop.run_in_executor(None, _get_exit_code_only, container_id)

    yield ("done", exit_code)


def stop_container(container_id: str) -> None:
    """
    Gracefully stop a running container (sends SIGTERM, waits 10s, then SIGKILL).
    Silently ignores if the container is already gone.
    """
    client = docker.from_env()
    try:
        container = client.containers.get(container_id)
        container.stop(timeout=10)
        logger.info("Stopped container %s", container_id[:12])
    except docker.errors.NotFound:
        pass  # already gone
    except Exception as exc:
        logger.warning("Error stopping container %s: %s", container_id[:12], exc)


def get_container_state(container_id: str) -> dict[str, object]:
    """
    Return best-effort container state for reconciliation logic.

    Shape:
      {
        "status": "running" | "exited" | "dead" | "not_found" | "unknown",
        "running": bool,
        "exit_code": int | None,
      }
    """
    client = docker.from_env()
    try:
        container = client.containers.get(container_id)
        container.reload()
        state = container.attrs.get("State", {}) or {}
        status = str(state.get("Status", "unknown") or "unknown")
        running = bool(state.get("Running", False))
        exit_code = state.get("ExitCode")
        return {
            "status": status,
            "running": running,
            "exit_code": int(exit_code) if isinstance(exit_code, int) else None,
        }
    except docker.errors.NotFound:
        return {
            "status": "not_found",
            "running": False,
            "exit_code": None,
        }
    except Exception as exc:
        logger.warning("Error reading container state for %s: %s", container_id[:12], exc)
        return {
            "status": "unknown",
            "running": False,
            "exit_code": None,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Progress parsing
# ─────────────────────────────────────────────────────────────────────────────

def parse_progress(line: str, patterns: list[str]) -> int | None:
    """
    Try each regex pattern against a log line and return a 0–100 integer if
    a match is found, otherwise None.

    Patterns with two capture groups are assumed to be X/Y fractions.
    Patterns with one capture group are assumed to be a bare percentage.
    """
    for pattern in patterns:
        m = re.search(pattern, line, re.IGNORECASE)
        if not m:
            continue
        groups = m.groups()
        try:
            numeric_groups = []
            for group in groups:
                if group is None:
                    continue
                if re.fullmatch(r"-?\d+", str(group).strip()):
                    numeric_groups.append(int(str(group).strip()))

            if len(numeric_groups) >= 2:
                current, total = numeric_groups[0], numeric_groups[1]
                if total > 0:
                    return min(100, int(current / total * 100))
            elif len(numeric_groups) == 1:
                return min(100, max(0, int(numeric_groups[0])))
        except (ValueError, ZeroDivisionError):
            pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_exit_code_only(container_id: str) -> int:
    """
    Retrieve the container's exit code without attempting removal.
    Useful when Docker auto-remove (--rm) is enabled.
    """
    client = docker.from_env()
    try:
        container = client.containers.get(container_id)
        container.reload()
        exit_code = container.attrs.get("State", {}).get("ExitCode", -1)
        logger.info("Container %s finished with exit_code=%s", container_id[:12], exit_code)
        return exit_code
    except docker.errors.NotFound:
        # Auto-remove may delete the container before this read; treat as unknown
        # failure rather than accidental success.
        return -1
    except Exception as exc:
        logger.warning("Error reading exit code for %s: %s", container_id[:12], exc)
        return -1
