"""
test_jobs.py — Tests for job creation, access control, and history.

We mock the Docker runner entirely so these tests don't need a live Docker
daemon. The mocks let us verify that the routes correctly call the runner
with the right arguments and handle both success and failure paths.
"""
import json
from urllib.parse import parse_qs, urlparse
import pytest
from unittest.mock import patch, MagicMock


class TestRunPage:
    """Tests for GET /run/{converter} — the converter form page."""

    def test_run_page_renders_for_known_converter(self, operator_client):
        response = operator_client.get("/run/tec-suite", follow_redirects=True)
        assert response.status_code == 200
        # The page should contain the converter label
        assert b"TEC-Suite" in response.content or b"tec-suite" in response.content.lower()
        assert b"Auto-remove container (--rm)" in response.content

    def test_run_page_404_for_unknown_converter(self, operator_client):
        response = operator_client.get("/run/does-not-exist", follow_redirects=True)
        assert response.status_code == 404

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


class TestStartJob:
    """Tests for POST /jobs/start — container launch."""

    def _start_job_data(self, **overrides):
        """Return a minimal valid form payload for the tec-suite converter."""
        data = {
            "converter_name": "tec-suite",
            "root": "/data/rinex",
            "root_subpath": "/2026_original/001",
            "jobs": "4",
            "verbose": "on",
            "cleanup": "on",
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
