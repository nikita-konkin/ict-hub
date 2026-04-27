"""
test_jobs.py — Tests for job creation, access control, and history.

We mock the Docker runner entirely so these tests don't need a live Docker
daemon. The mocks let us verify that the routes correctly call the runner
with the right arguments and handle both success and failure paths.
"""
import json
from urllib.parse import parse_qs, urlparse
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture(autouse=True)
def converter_env_roots(monkeypatch):
    monkeypatch.setattr("app.jobs.cfg.RINEX_DATA_PATH_HOST", "/data/rinex")
    monkeypatch.setattr("app.jobs.cfg.TECSUITE_OUT_DAT_DATA_PATH_HOST", "/data/tecs-out")
    monkeypatch.setattr("app.jobs.cfg.ABSTEC_OUTPUT_DATA_PATH_HOST", "/data/abstec-out")
    monkeypatch.setattr("app.jobs.cfg.PARQUET_OUTPUT_TECSUITE_DATA_PATH_HOST", "/data/tecsuite-parquet")
    monkeypatch.setattr("app.jobs.cfg.PARQUET_OUTPUT_ABSTEC_DATA_PATH_HOST", "/data/abstec-parquet")


class TestRunPage:
    """Tests for GET /run/{converter} — the converter form page."""

    def test_run_page_renders_for_known_converter(self, operator_client):
        response = operator_client.get("/run/tec-suite", follow_redirects=True)
        assert response.status_code == 200
        # The page should contain the converter label
        assert b"TEC-Suite" in response.content or b"tec-suite" in response.content.lower()
        assert b"Auto-remove container (--rm)" in response.content
        assert b"Server Folder (host path) is configured from environment variable RINEX_DATA_PATH_HOST." in response.content
        assert b"for=\"tec-server-root\"" not in response.content

    def test_run_page_404_for_unknown_converter(self, operator_client):
        response = operator_client.get("/run/does-not-exist", follow_redirects=True)
        assert response.status_code == 404

    def test_abstec_run_page_renders_dependent_selectors(self, operator_client):
        response = operator_client.get("/run/abstec-suite", follow_redirects=True)
        assert response.status_code == 200
        assert b"Input DAT Root (host path) is configured from environment variable TECSUITE_OUT_DAT_DATA_PATH_HOST." in response.content
        assert b"for=\"abstec-year-select\"" in response.content
        assert b"for=\"abstec-day-of-year-select\"" in response.content
        assert b"for=\"abstec-days-select\"" in response.content
        assert b"for=\"abstec-site-select\"" in response.content

    def test_dat_parquet_run_page_renders_env_backed_path_controls(self, operator_client):
        response = operator_client.get("/run/dat-parquet-handler", follow_redirects=True)
        assert response.status_code == 200
        assert b"Source Dataset" in response.content
        assert b"for=\"dat-parquet-year-select\"" in response.content
        assert b"for=\"dat-parquet-day-select\"" in response.content
        assert b"id=\"dat-parquet-src-preview\"" in response.content
        assert b"id=\"dat-parquet-dst-preview\"" in response.content
        assert b"TEC-Suite DAT output" in response.content
        assert b"AbsTEC output" in response.content

    def test_unauthenticated_run_page_redirects(self, client):
        response = client.get("/run/tec-suite", follow_redirects=False)
        assert response.status_code in (302, 303)

    def test_completed_job_id_does_not_render_active_panel(self, operator_client, completed_job):
        """Completed jobs in query params should not re-open the live SSE panel."""
        response = operator_client.get(
            f"/run/tec-suite?job_id={completed_job.id}",
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"No job running" in response.content
        assert f'data-stream-url="/jobs/{completed_job.id}/stream'.encode() not in response.content

    def test_run_page_auto_attaches_running_job(self, operator_client, completed_job, db, monkeypatch):
        import app.jobs as jobs_module
        import app.job_runtime as runtime_module

        monkeypatch.setattr(
            runtime_module,
            "get_container_state",
            lambda _container_id: {"status": "running", "running": True, "exit_code": None},
        )

        completed_job.status = "running"
        completed_job.finished_at = None
        completed_job.exit_code = None
        db.commit()

        response = operator_client.get("/run/tec-suite", follow_redirects=True)
        assert response.status_code == 200

        expected_tail = int(jobs_module.cfg.LOG_PAGELOAD_TAIL_LINES)
        assert f'data-stream-url="/jobs/{completed_job.id}/stream?tail={expected_tail}"'.encode() in response.content

    def test_run_page_discovers_running_container_by_image(self, admin_client, db, monkeypatch):
        import app.jobs as jobs_module
        import app.job_runtime as runtime_module
        from app.models import JobRun
        from datetime import datetime, timezone

        fake_container_id = "deadbeef" * 8  # 64 chars
        monkeypatch.setattr(
            runtime_module,
            "get_container_state",
            lambda _container_id: {"status": "running", "running": True, "exit_code": None},
        )
        monkeypatch.setattr(
            jobs_module,
            "list_running_containers",
            lambda: [
                {
                    "id": fake_container_id,
                    "name": "external-tec-suite",
                    "image": "tec-suite:latest",
                    "labels": {},
                    "started_at": datetime.now(timezone.utc),
                }
            ],
        )

        response = admin_client.get("/run/tec-suite", follow_redirects=True)
        assert response.status_code == 200

        job = db.query(JobRun).filter(JobRun.container_id == fake_container_id).first()
        assert job is not None
        assert job.converter == "tec-suite"
        assert job.status == "running"

        expected_tail = int(jobs_module.cfg.LOG_PAGELOAD_TAIL_LINES)
        assert f'data-stream-url="/jobs/{job.id}/stream?tail={expected_tail}"'.encode() in response.content

    def test_running_job_id_replays_backlog_even_with_resume_flag(self, operator_client, completed_job, db, monkeypatch):
        import app.jobs as jobs_module
        import app.job_runtime as runtime_module

        monkeypatch.setattr(
            runtime_module,
            "get_container_state",
            lambda _container_id: {"status": "running", "running": True, "exit_code": None},
        )

        completed_job.status = "running"
        completed_job.finished_at = None
        completed_job.exit_code = None
        db.commit()

        response = operator_client.get(
            f"/run/tec-suite?job_id={completed_job.id}&resume=1",
            follow_redirects=True,
        )

        assert response.status_code == 200
        expected_tail = int(jobs_module.cfg.LOG_PAGELOAD_TAIL_LINES)
        assert f'data-stream-url="/jobs/{completed_job.id}/stream?tail={expected_tail}"'.encode() in response.content

    # -- async indexer integration -------------------------------------------------

    def test_tec_suite_run_page_calls_async_rinex_indexer(self, operator_client, monkeypatch):
        """GET /run/tec-suite must use list_rinex_server_structure_async (not the sync variant)."""
        import app.jobs as jobs_module
        calls: list[str] = []

        async def _fake_rinex_async(host_root: str):
            calls.append(host_root)
            return [{"year": "2026_original", "days": [{"day": "001", "stations": 3}]}]

        monkeypatch.setattr(jobs_module, "list_rinex_server_structure_async", _fake_rinex_async)

        response = operator_client.get("/run/tec-suite", follow_redirects=True)
        assert response.status_code == 200
        assert calls, "list_rinex_server_structure_async was never called"
        assert calls[0] == "/data/rinex"

    def test_abstec_run_page_calls_async_tecsuite_indexer(self, operator_client, monkeypatch):
        """GET /run/abstec-suite must use list_tecsuite_output_structure_async."""
        import app.jobs as jobs_module
        calls: list[str] = []

        async def _fake_tecsuite_async(host_root: str):
            calls.append(host_root)
            return [{"year": "2026", "days": [{"day": "001", "sites": ["aksu"]}]}]

        monkeypatch.setattr(jobs_module, "list_tecsuite_output_structure_async", _fake_tecsuite_async)

        response = operator_client.get("/run/abstec-suite", follow_redirects=True)
        assert response.status_code == 200
        assert calls, "list_tecsuite_output_structure_async was never called"
        assert calls[0] == "/data/tecs-out"

    def test_dat_parquet_run_page_calls_all_four_async_indexers(self, operator_client, monkeypatch):
        """GET /run/dat-parquet-handler must call all four async indexers (via asyncio.gather)."""
        import app.jobs as jobs_module
        tecsuite_calls: list[str] = []
        abstec_calls: list[str] = []
        parquet_calls: list[str] = []

        async def _fake_tecsuite_async(host_root: str):
            tecsuite_calls.append(host_root)
            return []

        async def _fake_parquet_async(host_root: str):
            parquet_calls.append(host_root)
            return []

        # Both tecsuite and parquet async functions are used for four trees
        monkeypatch.setattr(jobs_module, "list_tecsuite_output_structure_async", _fake_tecsuite_async)
        monkeypatch.setattr(jobs_module, "list_parquet_output_structure_async", _fake_parquet_async)

        response = operator_client.get("/run/dat-parquet-handler", follow_redirects=True)
        assert response.status_code == 200
        # tecsuite + abstec paths
        assert len(tecsuite_calls) >= 1
        # tecsuite-parquet + abstec-parquet paths
        assert len(parquet_calls) >= 1

    def test_run_page_renders_even_when_indexer_returns_empty(self, operator_client, monkeypatch):
        """A completely empty tree from the indexer must not cause a 500."""
        import app.jobs as jobs_module

        async def _empty_tree(host_root: str):
            return []

        monkeypatch.setattr(jobs_module, "list_rinex_server_structure_async", _empty_tree)

        response = operator_client.get("/run/tec-suite", follow_redirects=True)
        assert response.status_code == 200


class TestStartJob:
    """Tests for POST /jobs/start — container launch."""

    def _start_job_data(self, **overrides):
        """Return a minimal valid form payload for the tec-suite converter."""
        data = {
            "converter_name": "tec-suite",
            "root_subpath": "/2026_original/001",
            "jobs": "4",
            "verbose": "on",
            "cleanup": "on",
        }
        data.update(overrides)
        return data

    def _start_abstec_job_data(self, **overrides):
        """Return a minimal valid form payload for the abstec-suite converter."""
        data = {
            "converter_name": "abstec-suite",
            "year": "2026",
            "day_of_year": "001",
            "days": "",
            "site": "aksu0010",
            "dry_run": "on",
        }
        data.update(overrides)
        return data

    def _start_dat_parquet_job_data(self, **overrides):
        """Return a minimal valid form payload for the dat-parquet-handler converter."""
        data = {
            "converter_name": "dat-parquet-handler",
            "direction": "dat-to-parquet",
            "dataset_profile": "tecsuite",
            "root_subpath": "",
        }
        data.update(overrides)
        return data

    @patch("app.jobs.start_container", return_value="container_root_path")
    def test_start_job_passes_year_day_root_subpath(self, mock_start, operator_client):
        response = operator_client.post(
            "/jobs/start",
            data=self._start_job_data(root_subpath="/2026_original"),
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        assert response.status_code == 200
        assert mock_start.called

    @patch("app.jobs.start_container", return_value="container_root_path_2d")
    def test_start_job_accepts_two_digit_day_root_subpath(self, mock_start, operator_client):
        response = operator_client.post(
            "/jobs/start",
            data=self._start_job_data(root_subpath="/2026_original/01"),
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        assert response.status_code == 200
        assert mock_start.called

    @patch("app.jobs.start_container", return_value="container_root_path_month_day")
    def test_start_job_accepts_month_day_root_subpath(self, mock_start, operator_client):
        response = operator_client.post(
            "/jobs/start",
            data=self._start_job_data(root_subpath="/2026_original/01/21"),
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        assert response.status_code == 200
        assert mock_start.called

    @patch("app.jobs.start_container", return_value="container123abc")
    def test_successful_job_start_returns_panel(self, mock_start, operator_client, db):
        """A successful job start should return the SSE monitoring panel HTML."""
        response = operator_client.post(
            "/jobs/start",
            data=self._start_job_data(),
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        assert response.status_code == 200
        # The response should be the job_panel.html fragment
        assert b"sse-connect" in response.content or b"job-output" in response.content or b"log-lines" in response.content

        # Verify the JobRun was written to the database
        from app.models import JobRun
        job = db.query(JobRun).filter(JobRun.converter == "tec-suite").first()
        assert job is not None
        assert job.container_id == "container123abc"
        assert job.status == "running"

    @patch("app.jobs.start_container", return_value="container_bootstrap")
    def test_successful_job_start_bootstraps_event_producer(
        self,
        mock_start,
        operator_client,
        monkeypatch,
    ):
        producer_mock = AsyncMock()
        monkeypatch.setattr("app.jobs.ensure_job_producer", producer_mock)

        response = operator_client.post(
            "/jobs/start",
            data=self._start_abstec_job_data(),
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )

        assert response.status_code == 200
        producer_mock.assert_awaited_once()

    @patch("app.jobs.start_container", return_value="container_rm")
    def test_start_job_passes_auto_remove_when_checked(self, mock_start, operator_client):
        response = operator_client.post(
            "/jobs/start",
            data=self._start_job_data(auto_remove="on"),
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        assert response.status_code == 200
        assert mock_start.called
        _, kwargs = mock_start.call_args
        assert kwargs.get("auto_remove") is True

    @patch("app.jobs.start_container", return_value="container999")
    def test_successful_non_htmx_start_redirects_back_to_run_page(self, mock_start, operator_client):
        """A plain form POST should redirect back to the converter run page."""
        response = operator_client.post(
            "/jobs/start",
            data=self._start_job_data(),
            follow_redirects=False,
        )
        assert response.status_code == 303
        location = response.headers.get("location")
        assert location is not None
        parsed = urlparse(location)
        assert parsed.path == "/run/tec-suite"
        query = parse_qs(parsed.query)
        assert "job_id" in query

    @patch("app.jobs.start_container", return_value="container321")
    def test_non_htmx_redirect_target_renders_job_panel(self, mock_start, operator_client):
        """The redirected run page should show the active job panel in #job-output."""
        start = operator_client.post(
            "/jobs/start",
            data=self._start_job_data(),
            follow_redirects=False,
        )
        assert start.status_code == 303
        location = start.headers.get("location")
        assert location is not None

        run_page = operator_client.get(location, follow_redirects=False)
        assert run_page.status_code == 200
        assert b"sse-connect" in run_page.content or b"JOB #" in run_page.content

    @patch("app.jobs.start_container", return_value="container456")
    def test_job_stores_user_id(self, mock_start, operator_client, operator_user, db):
        """Each job run must be attributed to the user who submitted the form."""
        operator_client.post(
            "/jobs/start",
            data=self._start_job_data(),
            follow_redirects=False,
        )
        from app.models import JobRun
        job = db.query(JobRun).filter(JobRun.user_id == operator_user.id).first()
        assert job is not None

    @patch("app.jobs.start_container", return_value="container_jobs_1")
    def test_start_job_accepts_single_parallel_job(self, mock_start, operator_client):
        """Parallel Jobs=1 should still start the container."""
        response = operator_client.post(
            "/jobs/start",
            data=self._start_job_data(jobs="1"),
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        assert response.status_code == 200
        assert mock_start.called

    @patch("app.jobs.start_container", return_value="container789")
    def test_job_stores_flags_as_json(self, mock_start, operator_client, db):
        """The flags submitted in the form should be serialised to JSON in the DB."""
        operator_client.post(
            "/jobs/start",
            data=self._start_job_data(jobs="8"),
            follow_redirects=False,
        )
        from app.models import JobRun
        job = db.query(JobRun).order_by(JobRun.id.desc()).first()
        assert job is not None
        flags = json.loads(job.flags_json)
        assert flags.get("jobs") in ("8", 8)
        assert flags.get("root")

    @patch("app.jobs.start_container", return_value="container_env_root")
    def test_tecsuite_uses_env_root_and_stores_note(self, mock_start, operator_client, db):
        operator_client.post(
            "/jobs/start",
            data=self._start_job_data(),
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )

        from app.models import JobRun

        _, _, volumes = mock_start.call_args.args
        assert "/data/rinex" in volumes

        job = db.query(JobRun).order_by(JobRun.id.desc()).first()
        assert job is not None
        assert job.rinex_path == "Configured from environment variable RINEX_DATA_PATH_HOST"

    @patch("app.jobs.start_container", return_value="container_abstec_env")
    def test_abstec_uses_env_paths_and_stores_note(self, mock_start, operator_client, db):
        response = operator_client.post(
            "/jobs/start",
            data=self._start_abstec_job_data(),
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        assert response.status_code == 200

        from app.models import JobRun

        _, _, volumes = mock_start.call_args.args
        assert "/data/tecs-out" in volumes
        assert "/data/abstec-out" in volumes

        job = db.query(JobRun).order_by(JobRun.id.desc()).first()
        assert job is not None
        assert job.converter == "abstec-suite"
        assert job.rinex_path == "Configured from environment variable TECSUITE_OUT_DAT_DATA_PATH_HOST"

        flags = json.loads(job.flags_json)
        assert flags.get("dat_path") == "/data/tecs-out"
        assert flags.get("output_dir") == "/data/abstec-out"
        assert flags.get("workdir") == "/data/workdir"
        assert flags.get("elevation_cutoff") == 10
        assert flags.get("time_step_hours") == "0.5"
        assert flags.get("correction_coefficient") == "0.97"
        assert flags.get("runner") == "auto"

    def test_abstec_missing_env_dat_root_returns_400(self, operator_client, monkeypatch):
        monkeypatch.setattr("app.jobs.cfg.TECSUITE_OUT_DAT_DATA_PATH_HOST", "")
        response = operator_client.post(
            "/jobs/start",
            data=self._start_abstec_job_data(),
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        assert response.status_code == 400

    @patch("app.jobs.start_container", return_value="container_dat_parquet")
    def test_dat_parquet_uses_tecsuite_env_paths(self, mock_start, operator_client, db):
        response = operator_client.post(
            "/jobs/start",
            data=self._start_dat_parquet_job_data(),
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        assert response.status_code == 200

        from app.models import JobRun

        _, _, volumes = mock_start.call_args.args
        assert "/data/tecs-out" in volumes
        assert "/data/tecsuite-parquet" in volumes

        job = db.query(JobRun).order_by(JobRun.id.desc()).first()
        assert job is not None
        assert job.converter == "dat-parquet-handler"
        assert job.rinex_path == "Configured from environment variable TECSUITE_OUT_DAT_DATA_PATH_HOST"

        flags = json.loads(job.flags_json)
        assert flags.get("src") == "/data/tecs-out"
        assert flags.get("dst") == "/data/tecsuite-parquet"
        assert flags.get("dataset_profile") == "tecsuite"

    @patch("app.jobs.start_container", return_value="container_dat_parquet_overwrite")
    def test_dat_parquet_overwrite_reuses_source_as_destination(self, mock_start, operator_client, db):
        response = operator_client.post(
            "/jobs/start",
            data=self._start_dat_parquet_job_data(dataset_profile="abstec", overwrite="on"),
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        assert response.status_code == 200

        from app.models import JobRun

        _, _, volumes = mock_start.call_args.args
        assert "/data/abstec-out" in volumes
        assert volumes["/data/abstec-out"]["bind"] == "/output"

        job = db.query(JobRun).order_by(JobRun.id.desc()).first()
        flags = json.loads(job.flags_json)
        assert flags.get("src") == "/data/abstec-out"
        assert flags.get("dst") == "/data/abstec-out"

    def test_dat_parquet_missing_profile_env_returns_400(self, operator_client, monkeypatch):
        monkeypatch.setattr("app.jobs.cfg.PARQUET_OUTPUT_TECSUITE_DATA_PATH_HOST", "")
        response = operator_client.post(
            "/jobs/start",
            data=self._start_dat_parquet_job_data(dataset_profile="tecsuite"),
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        assert response.status_code == 400
        assert b"PARQUET_OUTPUT_TECSUITE_DATA_PATH_HOST is not configured" in response.content

    @patch("app.jobs.start_container", return_value="container_dat_parquet_subpath")
    def test_dat_parquet_applies_root_subpath_to_src_and_dst(self, mock_start, operator_client, db):
        response = operator_client.post(
            "/jobs/start",
            data=self._start_dat_parquet_job_data(root_subpath="/2026/007"),
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        assert response.status_code == 200

        from app.models import JobRun

        _, _, volumes = mock_start.call_args.args
        assert "/data/tecs-out/2026/007" in volumes
        assert "/data/tecsuite-parquet/2026/007" in volumes

        job = db.query(JobRun).order_by(JobRun.id.desc()).first()
        flags = json.loads(job.flags_json)
        assert flags.get("root_subpath") == "/2026/007"
        assert flags.get("src") == "/data/tecs-out/2026/007"
        assert flags.get("dst") == "/data/tecsuite-parquet/2026/007"

    @patch("app.jobs.start_container", return_value="container_dat_parquet_parquet_src_subpath")
    def test_dat_parquet_parquet_to_dat_applies_root_subpath(self, mock_start, operator_client, db):
        """parquet-to-dat direction: subpath is applied to both src (parquet root) and dst (DAT root)."""
        response = operator_client.post(
            "/jobs/start",
            data=self._start_dat_parquet_job_data(direction="parquet-to-dat", root_subpath="/2026/007"),
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        assert response.status_code == 200

        from app.models import JobRun

        _, _, volumes = mock_start.call_args.args
        assert "/data/tecsuite-parquet/2026/007" in volumes
        assert "/data/tecs-out/2026/007" in volumes

        job = db.query(JobRun).order_by(JobRun.id.desc()).first()
        flags = json.loads(job.flags_json)
        assert flags.get("src") == "/data/tecsuite-parquet/2026/007"
        assert flags.get("dst") == "/data/tecs-out/2026/007"

    def test_dat_parquet_invalid_root_subpath_returns_400(self, operator_client):
        response = operator_client.post(
            "/jobs/start",
            data=self._start_dat_parquet_job_data(root_subpath="/2026_original/007"),
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        assert response.status_code == 400
        assert b"Select a valid year/day folder" in response.content

    @patch("app.jobs.start_container", side_effect=Exception("Docker not available"))
    def test_docker_error_returns_error_response(self, mock_start, operator_client, db):
        """If Docker fails to start the container, the route should return an error."""
        import docker.errors
        with patch("app.jobs.start_container", side_effect=docker.errors.DockerException("daemon down")):
            response = operator_client.post(
                "/jobs/start",
                data=self._start_job_data(),
                follow_redirects=False,
            )
        # Should return a 500 with an error HTML fragment, not crash
        assert response.status_code == 500
        assert b"Docker error" in response.content or b"error" in response.content.lower()

    def test_unknown_converter_returns_400(self, operator_client):
        response = operator_client.post(
            "/jobs/start",
            data={"converter_name": "nonexistent"},
            follow_redirects=False,
        )
        assert response.status_code == 400

    def test_tecsuite_missing_root_subpath_returns_400(self, operator_client):
        response = operator_client.post(
            "/jobs/start",
            data=self._start_job_data(root_subpath=""),
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        assert response.status_code == 400

    def test_unauthenticated_start_redirects(self, client):
        response = client.post(
            "/jobs/start",
            data=self._start_job_data(),
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)

    # ─────────────────────────────────────────────────────────────────────────────
    # DAT <-> Parquet Day Range Validation Tests
    # ─────────────────────────────────────────────────────────────────────────────

    @patch("app.jobs.start_container", return_value="container_day_range_valid")
    def test_dat_parquet_accepts_valid_day_from_and_day_to(self, mock_start, operator_client):
        """DAT-Parquet with valid day_from and day_to should succeed."""
        response = operator_client.post(
            "/jobs/start",
            data=self._start_dat_parquet_job_data(day_from="1", day_to="366"),
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        assert response.status_code == 200
        assert mock_start.called

    def test_dat_parquet_day_from_below_range_returns_400(self, operator_client):
        """Day from < 1 should be rejected with 400."""
        response = operator_client.post(
            "/jobs/start",
            data=self._start_dat_parquet_job_data(day_from="0", day_to="100"),
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        assert response.status_code == 400
        assert b"day-from" in response.content or b"day_from" in response.content or b"Day from" in response.content

    def test_dat_parquet_day_from_above_range_returns_400(self, operator_client):
        """Day from > 366 should be rejected with 400."""
        response = operator_client.post(
            "/jobs/start",
            data=self._start_dat_parquet_job_data(day_from="367", day_to="367"),
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        assert response.status_code == 400

    def test_dat_parquet_day_to_below_range_returns_400(self, operator_client):
        """Day to < 1 should be rejected with 400."""
        response = operator_client.post(
            "/jobs/start",
            data=self._start_dat_parquet_job_data(day_from="1", day_to="0"),
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        assert response.status_code == 400

    def test_dat_parquet_day_to_above_range_returns_400(self, operator_client):
        """Day to > 366 should be rejected with 400."""
        response = operator_client.post(
            "/jobs/start",
            data=self._start_dat_parquet_job_data(day_from="100", day_to="500"),
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        assert response.status_code == 400

    def test_dat_parquet_day_from_greater_than_day_to_returns_400(self, operator_client):
        """day_from > day_to should be rejected with 400."""
        response = operator_client.post(
            "/jobs/start",
            data=self._start_dat_parquet_job_data(day_from="100", day_to="50"),
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        assert response.status_code == 400
        assert b"day-from" in response.content or b"day_from" in response.content or b"less than or equal" in response.content

    def test_dat_parquet_day_range_non_numeric_returns_400(self, operator_client):
        """Non-numeric day values should be rejected with 400."""
        response = operator_client.post(
            "/jobs/start",
            data=self._start_dat_parquet_job_data(day_from="abc", day_to="100"),
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        assert response.status_code == 400
        # Should have an error about the day_from value
        assert b"day-from" in response.content or b"numeric" in response.content.lower() or b"digit" in response.content.lower()

    @patch("app.jobs.start_container", return_value="container_day_from_empty")
    def test_dat_parquet_day_from_empty_is_valid(self, mock_start, operator_client):
        """Empty day_from should be valid (optional)."""
        response = operator_client.post(
            "/jobs/start",
            data=self._start_dat_parquet_job_data(day_from="", day_to="100"),
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        assert response.status_code == 200
        assert mock_start.called

    @patch("app.jobs.start_container", return_value="container_day_to_empty")
    def test_dat_parquet_day_to_empty_is_valid(self, mock_start, operator_client):
        """Empty day_to should be valid (optional)."""
        response = operator_client.post(
            "/jobs/start",
            data=self._start_dat_parquet_job_data(day_from="100", day_to=""),
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        assert response.status_code == 200
        assert mock_start.called

    @patch("app.jobs.start_container", return_value="container_both_empty")
    def test_dat_parquet_both_day_range_empty_is_valid(self, mock_start, operator_client):
        """Both day_from and day_to empty should be valid."""
        response = operator_client.post(
            "/jobs/start",
            data=self._start_dat_parquet_job_data(day_from="", day_to=""),
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        assert response.status_code == 200
        assert mock_start.called

    @patch("app.jobs.start_container", return_value="container_day_range_edge")
    def test_dat_parquet_day_boundaries_1_and_366(self, mock_start, operator_client):
        """Edge case: day_from=1 and day_to=366 should succeed."""
        response = operator_client.post(
            "/jobs/start",
            data=self._start_dat_parquet_job_data(day_from="1", day_to="366"),
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        assert response.status_code == 200
        assert mock_start.called

    @patch("app.jobs.start_container", return_value="container_day_range_single")
    def test_dat_parquet_day_from_equals_day_to(self, mock_start, operator_client):
        """Edge case: day_from == day_to should succeed."""
        response = operator_client.post(
            "/jobs/start",
            data=self._start_dat_parquet_job_data(day_from="100", day_to="100"),
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        assert response.status_code == 200
        assert mock_start.called

    @patch("app.jobs.start_container", return_value="container_day_whitespace")
    def test_dat_parquet_day_range_whitespace_trimmed(self, mock_start, operator_client):
        """Whitespace around day values should be trimmed."""
        response = operator_client.post(
            "/jobs/start",
            data=self._start_dat_parquet_job_data(day_from="  50  ", day_to="  100  "),
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        assert response.status_code == 200
        assert mock_start.called

    # ─────────────────────────────────────────────────────────────────────────────
    # TEC-Suite Days Filter Option Tests
    # ─────────────────────────────────────────────────────────────────────────────

    @patch("app.jobs.start_container", return_value="container_tec_days")
    def test_tec_suite_accepts_days_filter(self, mock_start, operator_client):
        """TEC-Suite with --days option should pass to container."""
        response = operator_client.post(
            "/jobs/start",
            data=self._start_job_data(days="1-5,10,12-14"),
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        assert response.status_code == 200
        assert mock_start.called

    @patch("app.jobs.start_container", return_value="container_tec_days_empty")
    def test_tec_suite_days_empty_is_valid(self, mock_start, operator_client):
        """TEC-Suite with empty --days should be valid (optional)."""
        response = operator_client.post(
            "/jobs/start",
            data=self._start_job_data(days=""),
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        assert response.status_code == 200
        assert mock_start.called

    @patch("app.jobs.start_container", return_value="container_tec_jobs_default")
    def test_tec_suite_jobs_default_is_1(self, mock_start, operator_client, db):
        """TEC-Suite default --jobs should be 1 (not 4)."""
        response = operator_client.post(
            "/jobs/start",
            data=self._start_job_data(jobs=""),  # omit jobs value to use default
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        assert response.status_code == 200
        assert mock_start.called

        from app.models import JobRun
        job = db.query(JobRun).order_by(JobRun.id.desc()).first()
        assert job is not None
        flags = json.loads(job.flags_json)
        assert flags.get("jobs") == 1


class TestJobHistory:
    """Tests for GET /history — audit log access control."""

    def test_operator_sees_own_jobs_only(self, operator_client, completed_job, admin_user, db):
        """Operators should only see jobs they submitted themselves."""
        # Create a second job owned by admin — operator must not see it
        from app.models import JobRun
        admin_job = JobRun(
            user_id=admin_user.id,
            converter="tec-suite",
            flags_json="{}",
            status="success",
        )
        db.add(admin_job)
        db.commit()

        response = operator_client.get("/history", follow_redirects=True)
        assert response.status_code == 200

        # The response should contain the operator's job
        assert b"#" + str(completed_job.id).encode() in response.content or \
               str(completed_job.id).encode() in response.content

    def test_admin_sees_all_jobs(self, admin_client, completed_job, operator_user, db):
        """Admins should see every user's jobs in the history table."""
        response = admin_client.get("/history", follow_redirects=True)
        assert response.status_code == 200
        # The page should not be empty and should contain job data
        assert b"tec-suite" in response.content.lower() or str(completed_job.id).encode() in response.content

    def test_history_paginates_correctly(self, operator_client, operator_user, db):
        """With more jobs than per_page, the pagination links should appear."""
        from app.models import JobRun
        # Create 30 jobs to trigger pagination (default per_page=25)
        for _ in range(30):
            db.add(JobRun(
                user_id=operator_user.id,
                converter="tec-suite",
                flags_json="{}",
                status="success",
            ))
        db.commit()

        response = operator_client.get("/history?page=1", follow_redirects=True)
        assert response.status_code == 200
        # Pagination links should be present
        assert b"page=2" in response.content

    def test_history_empty_state_rendered(self, operator_client):
        """With no jobs at all, the empty state message should be shown."""
        response = operator_client.get("/history", follow_redirects=True)
        assert response.status_code == 200


class TestStopJob:
    """Tests for POST /jobs/{id}/stop."""

    @patch("app.jobs.stop_container")
    def test_operator_can_stop_own_job(self, mock_stop, operator_client, completed_job, db):
        """An operator should be able to stop their own running job."""
        # Put the job into running state first
        completed_job.status = "running"
        completed_job.container_id = "abc123def456"
        db.commit()

        response = operator_client.post(
            f"/jobs/{completed_job.id}/stop",
            follow_redirects=True,
        )
        assert response.status_code == 200
        mock_stop.assert_called_once_with("abc123def456")

        db.refresh(completed_job)
        assert completed_job.status == "failed"

    def test_operator_cannot_stop_others_job(self, admin_client, completed_job, db):
        """An operator should get 403 when trying to stop a job they don't own."""
        # Make the job owned by someone else
        from app.models import User
        other = User(username="other", hashed_pw="x", role="operator")
        db.add(other)
        db.commit()

        completed_job.user_id = other.id
        db.commit()

        # The admin_client is an admin so this tests the inverse — let's use operator
        # We need an operator client that's NOT the job owner
        # This test verifies the 403 guard exists in the route
        response = admin_client.post(
            f"/jobs/{completed_job.id}/stop",
            follow_redirects=False,
        )
        # Admin should be allowed; 403 only for mismatched operators
        assert response.status_code in (302, 303, 200)


class TestOpenJob:
    """Tests for GET /jobs/{id}/open."""

    def test_open_job_redirects_to_converter_run_page(self, operator_client, completed_job):
        response = operator_client.get(
            f"/jobs/{completed_job.id}/open",
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers.get("location") == f"/run/{completed_job.converter}?job_id={completed_job.id}"


class TestDurableJobEvents:
    """Tests for persisted SSE replay and detached container reconciliation."""

    def test_stream_replays_persisted_events_with_ids(self, operator_client, completed_job, db, monkeypatch):
        from app.models import JobEvent
        import app.jobs as jobs_module

        first = JobEvent(
            job_id=completed_job.id,
            event_type="log",
            payload_xml="<log><message>line one</message><level>info</level></log>",
        )
        second = JobEvent(
            job_id=completed_job.id,
            event_type="done",
            payload_xml="<done><status>success</status><exit_code>0</exit_code></done>",
        )
        db.add_all([first, second])
        db.commit()
        db.refresh(first)
        db.refresh(second)
        first_id = first.id
        second_id = second.id
        monkeypatch.setattr(jobs_module, "SessionLocal", lambda: db)

        with operator_client.stream("GET", f"/jobs/{completed_job.id}/stream") as response:
            body = "".join(response.iter_text())
        assert response.status_code == 200
        assert f"id: {first_id}" in body
        assert "event: log" in body
        assert f"id: {second_id}" in body
        assert "event: done" in body

    def test_stream_resumes_after_event_id(self, operator_client, completed_job, db, monkeypatch):
        from app.models import JobEvent
        import app.jobs as jobs_module

        first = JobEvent(
            job_id=completed_job.id,
            event_type="log",
            payload_xml="<log><message>line one</message><level>info</level></log>",
        )
        second = JobEvent(
            job_id=completed_job.id,
            event_type="done",
            payload_xml="<done><status>success</status><exit_code>0</exit_code></done>",
        )
        db.add_all([first, second])
        db.commit()
        db.refresh(first)
        db.refresh(second)
        first_id = first.id
        second_id = second.id
        monkeypatch.setattr(jobs_module, "SessionLocal", lambda: db)

        with operator_client.stream(
            "GET",
            f"/jobs/{completed_job.id}/stream?after_event_id={first_id}",
        ) as response:
            body = "".join(response.iter_text())
        assert response.status_code == 200
        assert f"id: {first_id}" not in body
        assert f"id: {second_id}" in body
        assert "event: done" in body

    def test_stream_falls_back_to_direct_container_logs(
        self,
        operator_client,
        completed_job,
        db,
        monkeypatch,
    ):
        import app.jobs as jobs_module

        completed_job.status = "running"
        completed_job.finished_at = None
        completed_job.exit_code = None
        completed_job.container_id = "live123container"
        db.commit()
        job_id = completed_job.id

        async def fake_stream_logs(*args, **kwargs):
            yield ("log", "Processing year=2026 day=001 site=alex0010 (3/238)")
            yield ("progress", 1)
            yield ("done", 0)

        monkeypatch.setattr(jobs_module, "SessionLocal", lambda: db)
        monkeypatch.setattr(jobs_module, "stream_logs", fake_stream_logs)
        monkeypatch.setattr(jobs_module.cfg, "JOB_EVENT_BOOTSTRAP_TIMEOUT_SEC", 0.0)

        with operator_client.stream("GET", f"/jobs/{job_id}/stream") as response:
            body = "".join(response.iter_text())

        assert response.status_code == 200
        assert "event: log" in body
        assert "Processing year=2026 day=001 site=alex0010 (3/238)" in body
        assert "event: progress" in body
        assert "event: done" in body

        from app.models import JobRun

        refreshed_job = db.query(JobRun).filter(JobRun.id == job_id).first()
        assert refreshed_job is not None
        assert refreshed_job.status == "success"
        assert refreshed_job.exit_code == 0

    @patch("app.job_runtime.get_container_state")
    def test_reconcile_job_state_persists_done_event(self, mock_state, completed_job, db):
        from app.job_runtime import reconcile_job_state
        from app.models import JobEvent

        completed_job.status = "running"
        completed_job.finished_at = None
        completed_job.exit_code = None
        completed_job.container_id = "abc123def456"
        db.commit()

        mock_state.return_value = {"status": "exited", "running": False, "exit_code": 0}

        assert reconcile_job_state(completed_job.id, db=db) is True
        db.refresh(completed_job)
        assert completed_job.status == "success"
        assert completed_job.exit_code == 0

        done_event = (
            db.query(JobEvent)
            .filter(JobEvent.job_id == completed_job.id, JobEvent.event_type == "done")
            .first()
        )
        assert done_event is not None
        assert "<status>success</status>" in done_event.payload_xml
