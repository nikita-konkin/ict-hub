"""Tests for background TEC map animation jobs."""

from __future__ import annotations

import threading
import time

import pytest

import app.tec_map as tec_map_module
from app import tec_map_jobs
from app.tec_map_jobs import JobLimitError, RenderResult

QUERY = [
    ("date", "2026-01-02"),
    ("stations", "aksu"),
    ("stations", "alme"),
    ("start_time", "00:00:00"),
    ("end_time", "23:00:00"),
]


@pytest.fixture(autouse=True)
def _clean_jobs(monkeypatch):
    monkeypatch.setattr(tec_map_module.cfg, "PARQUET_OUTPUT_TECSUITE_DATA_PATH_CONTAINER", "/mnt/fake")
    yield
    with tec_map_jobs._lock:
        tec_map_jobs._jobs.clear()


def _wait_until_finished(client, status_url: str) -> dict:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        status = client.get(status_url).json()
        if status["status"] in ("done", "failed"):
            return status
        time.sleep(0.05)
    raise AssertionError(f"job did not finish: {status}")


def test_job_renders_in_background_and_serves_the_file(admin_client, monkeypatch):
    def fake_render(animation, progress=None):
        progress("loading", 1, 1)
        progress("drawing", 92, 92)
        return RenderResult(b"GIF89a-test", "image/gif", "tec_map.gif")

    monkeypatch.setattr(tec_map_module, "render_animation", fake_render)

    started = admin_client.post("/tec-map/gif/jobs", params=QUERY)
    assert started.status_code == 202
    assert started.json()["status"] in ("queued", "running", "done")

    status = _wait_until_finished(admin_client, started.json()["status_url"])
    assert status["status"] == "done"
    assert (status["stage"], status["done"], status["total"]) == ("done", 92, 92)

    result = admin_client.get(status["result_url"])
    assert result.status_code == 200
    assert result.content == b"GIF89a-test"
    assert result.headers["content-type"] == "image/gif"
    assert result.headers["content-disposition"].startswith("inline")


def test_same_request_reuses_the_job(admin_client, monkeypatch):
    monkeypatch.setattr(
        tec_map_module, "render_animation", lambda animation, progress=None: RenderResult(b"x", "image/gif", "a.gif")
    )

    first = admin_client.post("/tec-map/gif/jobs", params=QUERY).json()
    second = admin_client.post("/tec-map/gif/jobs", params=list(reversed(QUERY))).json()

    assert first["job_id"] == second["job_id"]


def test_failed_job_reports_a_readable_error(admin_client, monkeypatch):
    def fail(animation, progress=None):
        raise FileNotFoundError("No samples found for the requested stations/time range.")

    monkeypatch.setattr(tec_map_module, "render_animation", fail)

    started = admin_client.post("/tec-map/gif/jobs", params=QUERY).json()
    status = _wait_until_finished(admin_client, started["status_url"])

    assert status["status"] == "failed"
    assert status["error"] == "No samples found for the requested stations/time range."
    assert admin_client.get(f"/tec-map/gif/jobs/{started['job_id']}/result").status_code == 409


def test_bad_request_is_rejected_before_queueing(admin_client):
    response = admin_client.post(
        "/tec-map/gif/jobs", params=[*QUERY, ("frame_minutes", "1"), ("end_date", "2026-01-30")]
    )

    assert response.status_code == 413
    assert "frames" in response.json()["detail"]
    assert not tec_map_jobs._jobs


def test_other_users_jobs_are_hidden(operator_client):
    job = tec_map_jobs.submit(-1, "key", lambda progress: RenderResult(b"x", "image/gif", "a.gif"))

    assert operator_client.get(f"/tec-map/gif/jobs/{job.id}").status_code == 404
    assert operator_client.get(f"/tec-map/gif/jobs/{job.id}/result").status_code == 404


def test_active_jobs_per_user_are_limited():
    release = threading.Event()

    def blocked(progress):
        release.wait(10)
        return RenderResult(b"x", "image/gif", "a.gif")

    try:
        for number in range(tec_map_jobs.MAX_ACTIVE_JOBS_PER_USER):
            tec_map_jobs.submit(-2, f"key-{number}", blocked)
        with pytest.raises(JobLimitError):
            tec_map_jobs.submit(-2, "one-more", blocked)
        # The same request is not a new job, so it is not refused.
        assert tec_map_jobs.submit(-2, "key-0", blocked).key == "key-0"
    finally:
        release.set()
