"""
Tests for recording job outcomes independently of the browser:
  - app.job_monitor (record_job_exit, reconcile_running_jobs, watcher thread)
  - app.runner.wait_for_exit / _get_exit_code_only
  - the SSE stream no longer overwriting an outcome that is already recorded
"""

import json
from unittest.mock import MagicMock, patch

import docker.errors
import pytest

from app import job_monitor
from app.models import JobRun


class _NoCloseSession:
    """Route code that opens its own SessionLocal() into the test transaction."""

    def __init__(self, session):
        self._session = session

    def __getattr__(self, name):
        return getattr(self._session, name)

    def close(self):
        pass


@pytest.fixture
def monitor_db(db, monkeypatch):
    monkeypatch.setattr(job_monitor, "SessionLocal", lambda: _NoCloseSession(db))
    return db


@pytest.fixture
def running_job(db, operator_user):
    job = JobRun(
        user_id=operator_user.id,
        converter="tec-suite",
        flags_json=json.dumps({"auto_remove": True}),
        container_id="c0ffee",
        status="running",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


# ─────────────────────────────────────────────────────────────────────────────
# record_job_exit
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("exit_code", "status"), [(0, "success"), (3, "failed")])
def test_record_job_exit_sets_outcome(monitor_db, running_job, exit_code, status):
    job_monitor.record_job_exit(running_job.id, exit_code)

    monitor_db.refresh(running_job)
    assert running_job.status == status
    assert running_job.exit_code == exit_code
    assert running_job.finished_at is not None


def test_record_job_exit_unknown_code_is_error(monitor_db, running_job):
    job_monitor.record_job_exit(running_job.id, None)

    monitor_db.refresh(running_job)
    assert running_job.status == "error"
    assert running_job.exit_code == -1


def test_record_job_exit_keeps_user_stop(monitor_db, running_job):
    running_job.status = "failed"
    running_job.exit_code = -2  # stopped by user
    monitor_db.commit()

    job_monitor.record_job_exit(running_job.id, 0)

    monitor_db.refresh(running_job)
    assert running_job.status == "failed"
    assert running_job.exit_code == -2


# ─────────────────────────────────────────────────────────────────────────────
# Watcher thread and startup reconciliation
# ─────────────────────────────────────────────────────────────────────────────


def test_watcher_records_exit_code(monitor_db, running_job, monkeypatch):
    monkeypatch.setattr(job_monitor, "wait_for_exit", lambda container_id, auto_remove: 7)

    job_monitor._watch_job(running_job.id, "c0ffee", True)

    monitor_db.refresh(running_job)
    assert running_job.status == "failed"
    assert running_job.exit_code == 7


def test_watcher_leaves_job_alone_when_docker_is_unreachable(monitor_db, running_job, monkeypatch):
    def _unreachable(container_id, auto_remove):
        raise docker.errors.DockerException("daemon down")

    monkeypatch.setattr(job_monitor, "wait_for_exit", _unreachable)

    job_monitor._watch_job(running_job.id, "c0ffee", True)

    monitor_db.refresh(running_job)
    assert running_job.status == "running"


def test_start_job_watcher_runs_once_per_job(monkeypatch):
    started = []
    monkeypatch.setattr(
        job_monitor.threading, "Thread", lambda **kw: MagicMock(start=lambda: started.append(kw["args"]))
    )
    monkeypatch.setattr(job_monitor, "_watched_jobs", set())

    job_monitor.start_job_watcher(41, "abc", False)
    job_monitor.start_job_watcher(41, "abc", False)

    assert started == [(41, "abc", False)]


def test_reconcile_attaches_watchers_and_closes_containerless_jobs(monitor_db, running_job, operator_user, monkeypatch):
    orphan = JobRun(user_id=operator_user.id, converter="tec-suite", flags_json="{}", status="running")
    monitor_db.add(orphan)
    monitor_db.commit()
    watched = []
    monkeypatch.setattr(job_monitor, "start_job_watcher", lambda *args: watched.append(args))

    job_monitor.reconcile_running_jobs()

    assert watched == [(running_job.id, "c0ffee", True)]
    monitor_db.refresh(orphan)
    assert orphan.status == "error"


# ─────────────────────────────────────────────────────────────────────────────
# Runner helpers
# ─────────────────────────────────────────────────────────────────────────────


class TestWaitForExit:
    @patch("app.runner.docker.from_env")
    def test_auto_removed_container_waits_for_removal(self, mock_from_env):
        from app.runner import wait_for_exit

        mock_from_env.return_value.api.wait.return_value = {"StatusCode": 5}

        assert wait_for_exit("abc", auto_remove=True) == 5
        mock_from_env.return_value.api.wait.assert_called_once_with("abc", timeout=None, condition="removed")

    @patch("app.runner.docker.from_env")
    def test_regular_container_waits_until_not_running(self, mock_from_env):
        from app.runner import wait_for_exit

        mock_from_env.return_value.api.wait.return_value = {"StatusCode": 0}

        assert wait_for_exit("abc") == 0
        mock_from_env.return_value.api.wait.assert_called_once_with("abc", timeout=None, condition="not-running")

    @patch("app.runner.docker.from_env")
    def test_missing_container_returns_none(self, mock_from_env):
        from app.runner import wait_for_exit

        mock_from_env.return_value.api.wait.side_effect = docker.errors.NotFound("gone")

        assert wait_for_exit("abc", auto_remove=True) is None


@patch("app.runner.docker.from_env")
def test_exit_code_of_running_container_is_unknown(mock_from_env):
    """A running container reports State.ExitCode 0 — that must not read as success."""
    from app.runner import _get_exit_code_only

    container = MagicMock()
    container.attrs = {"State": {"Running": True, "ExitCode": 0}}
    mock_from_env.return_value.containers.get.return_value = container

    assert _get_exit_code_only("abc") is None


# ─────────────────────────────────────────────────────────────────────────────
# SSE stream
# ─────────────────────────────────────────────────────────────────────────────


def _fake_stream(*events):
    async def _stream(*args, **kwargs):
        for event in events:
            yield event

    return _stream


def _stream_job(operator_client, db, job, monkeypatch, *events):
    monkeypatch.setattr("app.jobs.stream_logs", _fake_stream(*events))
    monkeypatch.setattr("app.jobs.SessionLocal", lambda: _NoCloseSession(db))
    response = operator_client.get(f"/jobs/{job.id}/stream")
    db.refresh(job)
    return response


def test_stream_records_outcome_of_running_job(operator_client, db, running_job, monkeypatch):
    response = _stream_job(operator_client, db, running_job, monkeypatch, ("done", 0))

    assert "Success" in response.text
    assert running_job.status == "success"


def test_stream_does_not_overwrite_user_stop(operator_client, db, running_job, monkeypatch):
    running_job.status = "failed"
    running_job.exit_code = -2
    db.commit()

    _stream_job(operator_client, db, running_job, monkeypatch, ("done", 143))

    assert running_job.exit_code == -2


def test_interrupted_stream_leaves_status_to_watcher(operator_client, db, running_job, monkeypatch):
    response = _stream_job(
        operator_client,
        db,
        running_job,
        monkeypatch,
        ("error", "connection reset"),
        ("done", None),
    )

    assert "Log stream interrupted" in response.text
    assert running_job.status == "running"
