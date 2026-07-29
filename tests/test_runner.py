"""
test_runner.py — Unit tests for the Docker runner and converter registry.

These tests are pure unit tests: no HTTP requests, no database, no Docker.
They exercise the pure functions that do progress parsing, command building,
and volume mapping — the logic most likely to break if the registry changes.
"""
import pytest
from unittest.mock import MagicMock, patch, call


# ─────────────────────────────────────────────────────────────────────────────
# Progress parsing
# ─────────────────────────────────────────────────────────────────────────────

class TestParseProgress:
    """Tests for runner.parse_progress — the log-line → percentage extractor."""

    from app.runner import parse_progress

    # These patterns come from the tecsuite registry entry
    PATTERNS = [
        r"Day\s+(\d+)\s*/\s*(\d+)",
        r"Processing.*?(\d+)\s*/\s*(\d+)",
        r"(\d+)\s*/\s*(\d+)\s+days",
        r"(-?\d+)\s*%",
    ]

    def test_day_fraction_pattern(self):
        from app.runner import parse_progress
        assert parse_progress("Day 3/14", self.PATTERNS) == 21  # 3/14 * 100 ≈ 21

    def test_day_fraction_exact_half(self):
        from app.runner import parse_progress
        assert parse_progress("Day 5/10", self.PATTERNS) == 50

    def test_processing_fraction_pattern(self):
        from app.runner import parse_progress
        result = parse_progress("Processing archive 10/20", self.PATTERNS)
        assert result == 50

    def test_days_suffix_pattern(self):
        from app.runner import parse_progress
        result = parse_progress("7/10 days processed", self.PATTERNS)
        assert result == 70

    def test_bare_percentage_pattern(self):
        from app.runner import parse_progress
        assert parse_progress("45% complete", self.PATTERNS) == 45

    def test_percentage_capped_at_100(self):
        from app.runner import parse_progress
        # A rogue log line should not produce >100
        assert parse_progress("150% done", self.PATTERNS) == 100

    def test_percentage_floored_at_0(self):
        from app.runner import parse_progress
        # Negative percentages are clamped
        assert parse_progress("-5%", self.PATTERNS) == 0

    def test_no_match_returns_none(self):
        from app.runner import parse_progress
        assert parse_progress("No progress info here", self.PATTERNS) is None
        assert parse_progress("", self.PATTERNS) is None
        assert parse_progress("Starting...", self.PATTERNS) is None

    def test_zero_total_does_not_raise(self):
        from app.runner import parse_progress
        # "0/0" should return None rather than ZeroDivisionError
        result = parse_progress("Day 0/0", self.PATTERNS)
        assert result is None

    def test_case_insensitive_matching(self):
        from app.runner import parse_progress
        # "day" in various casings should all match
        assert parse_progress("DAY 2/8", self.PATTERNS) == 25
        assert parse_progress("day 2/8", self.PATTERNS) == 25

    def test_three_groups_uses_first_two_numeric_values(self):
        from app.runner import parse_progress
        patterns = [r"Completed\s+(\d+)\s*/\s*(\d+):\s+([\w.]+)"]
        assert parse_progress("Completed 101/132: spas0010.zip", patterns) == 76


# ─────────────────────────────────────────────────────────────────────────────
# Registry — get_converter
# ─────────────────────────────────────────────────────────────────────────────

class TestGetConverter:
    """Tests for registry.get_converter."""

    def test_returns_tecsuite_config(self):
        from app.registry import get_converter
        conv = get_converter("tec-suite")
        assert conv is not None
        assert conv["label"] == "TEC-Suite"
        assert "flags" in conv
        assert "image" in conv

    def test_returns_none_for_unknown(self):
        from app.registry import get_converter
        assert get_converter("does_not_exist") is None
        assert get_converter("") is None


# ─────────────────────────────────────────────────────────────────────────────
# Registry — build_command
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildCommand:
    """Tests for registry.build_command — converts form data into a CLI command."""

    def _form(self, **kwargs):
        """Minimal valid form data for tecsuite."""
        base = {
            "root": "N:\\RINEX",
            "root_subpath": "/2026_original/001",
            "jobs": "10",
            "verbose": True,
            "cleanup": True,
        }
        base.update(kwargs)
        return base

    def test_volume_flags_produce_volumes_dict(self):
        from app.registry import build_command
        _, volumes = build_command("tec-suite", self._form())
        # Root host path should appear as a volume key
        assert "N:\\RINEX" in volumes

    def test_rinex_volume_is_readwrite(self):
        from app.registry import build_command
        _, volumes = build_command("tec-suite", self._form())
        assert volumes["N:\\RINEX"]["mode"] == "rw"

    def test_rinex_container_path_in_command(self):
        from app.registry import build_command
        cmd, _ = build_command("tec-suite", self._form())
        # The command should reference selected container-side subpath, not host path
        assert "--root" in cmd
        assert "/data/rinex/2026_original/001" in cmd

    def test_root_subpath_year_only_in_command(self):
        from app.registry import build_command
        cmd, _ = build_command("tec-suite", self._form(root_subpath="/2026_original"))
        assert "/data/rinex/2026_original" in cmd

    def test_out_flag_not_present_for_tecsuite(self):
        from app.registry import build_command
        cmd, _ = build_command("tec-suite", self._form())
        assert "-o" not in cmd

    def test_output_volume_added_from_env_when_configured(self, monkeypatch):
        import app.registry as registry_module

        monkeypatch.setattr(registry_module.cfg, "TECSUITE_OUT_DAT_DATA_PATH_HOST", "N:\\RINEX\\out")
        monkeypatch.setattr(registry_module.cfg, "TECSUITE_OUT_DAT_DATA_PATH", "/app/out")

        _, volumes = registry_module.build_command("tec-suite", self._form())
        assert "N:\\RINEX\\out" in volumes
        assert volumes["N:\\RINEX\\out"]["bind"] == "/app/out"

    def test_jobs_flag_appears_in_command(self):
        from app.registry import build_command
        cmd, _ = build_command("tec-suite", self._form(jobs="10"))
        assert "-j" in cmd
        assert "10" in cmd

    def test_jobs_flag_accepts_one(self):
        from app.registry import build_command
        cmd, _ = build_command("tec-suite", self._form(jobs="1"))
        assert "-j" in cmd
        assert "1" in cmd

    def test_verbose_flag_appears_when_true(self):
        from app.registry import build_command
        cmd, _ = build_command("tec-suite", self._form(verbose=True))
        assert "-v" in cmd

    def test_verbose_flag_absent_when_false(self):
        from app.registry import build_command
        cmd, _ = build_command("tec-suite", self._form(verbose=False))
        assert "-v" not in cmd

    def test_cleanup_flag_appears_when_true(self):
        from app.registry import build_command
        cmd, _ = build_command("tec-suite", self._form(cleanup=True))
        assert "-k" in cmd

    def test_config_path_always_present(self):
        from app.registry import build_command
        cmd, _ = build_command("tec-suite", self._form())
        assert "-c" in cmd
        assert "/app/tecs.cfg" in cmd

    def test_tecs_script_path_always_present(self):
        from app.registry import build_command
        cmd, _ = build_command("tec-suite", self._form())
        assert "-t" in cmd
        assert "/app/tecs.py" in cmd

    def test_empty_host_path_skipped(self):
        """If the user leaves the path blank, no volume should be added for it."""
        from app.registry import build_command
        cmd, volumes = build_command("tec-suite", self._form(root=""))
        assert "N:\\RINEX" not in volumes

    def test_dat_parquet_uses_input_output_container_paths(self):
        from app.registry import build_command

        cmd, volumes = build_command(
            "dat-parquet-handler",
            {
                "direction": "dat-to-parquet",
                "src": "N:\\RINEX\\out",
                "dst": "N:\\RINEX\\parquet",
                "overwrite": False,
            },
        )

        assert "N:\\RINEX\\out" in volumes
        assert "N:\\RINEX\\parquet" in volumes
        assert volumes["N:\\RINEX\\out"]["bind"] == "/input"
        assert volumes["N:\\RINEX\\parquet"]["bind"] == "/output"
        assert "/input" in cmd
        assert "/output" in cmd


class TestRinexServerStructure:
    """Tests for data_indexer_client.list_rinex_server_structure."""

    def test_parses_xml_payload(self, monkeypatch):
        from app import data_indexer_client as client

        client._cache.clear()
        monkeypatch.setattr(client, "DATA_INDEXER_URL", "http://data-indexer:5001")

        xml = (
            '<?xml version="1.0" encoding="UTF-8" ?>'
            '<rinex_structure><item><year>2026_original</year><days>'
            '<item><day>01</day><stations>1</stations></item>'
            '<item><day>365</day><stations>2</stations></item>'
            '</days></item></rinex_structure>'
        )

        class _Resp:
            text = xml
            status_code = 200
            headers: dict = {}

            @staticmethod
            def raise_for_status():
                return None

        monkeypatch.setattr(client.httpx, "get", lambda url, timeout, **kw: _Resp())

        tree = client.list_rinex_server_structure("/mnt/rinex-server")
        assert tree == [{"year": "2026_original", "days": [{"day": "01", "stations": 1}, {"day": "365", "stations": 2}]}]

    def test_returns_empty_when_service_not_configured(self, monkeypatch):
        from app import data_indexer_client as client

        client._cache.clear()
        monkeypatch.setattr(client, "DATA_INDEXER_URL", "")
        tree = client.list_rinex_server_structure("/mnt/rinex-server")
        assert tree == []


class TestTecsuiteOutputStructure:
    """Tests for data_indexer_client.list_tecsuite_output_structure."""

    def test_parses_xml_payload(self, monkeypatch):
        from app import data_indexer_client as client

        client._cache.clear()
        monkeypatch.setattr(client, "DATA_INDEXER_URL", "http://data-indexer:5001")

        xml = (
            '<?xml version="1.0" encoding="UTF-8" ?>'
            '<tecsuite_structure><item><year>2026</year><days>'
            '<item><day>001</day><sites><item>aksu</item></sites></item>'
            '<item><day>002</day><sites><item>cher</item></sites></item>'
            '</days></item></tecsuite_structure>'
        )

        class _Resp:
            text = xml
            status_code = 200
            headers: dict = {}

            @staticmethod
            def raise_for_status():
                return None

        monkeypatch.setattr(client.httpx, "get", lambda url, timeout, **kw: _Resp())

        tree = client.list_tecsuite_output_structure("/mnt/tecsuite-out")
        assert tree == [{"year": "2026", "days": [{"day": "001", "sites": ["aksu"]}, {"day": "002", "sites": ["cher"]}]}]

    def test_returns_empty_when_service_not_configured(self, monkeypatch):
        from app import data_indexer_client as client

        client._cache.clear()
        monkeypatch.setattr(client, "DATA_INDEXER_URL", "")
        tree = client.list_tecsuite_output_structure("/mnt/tecsuite-out")
        assert tree == []


# ─────────────────────────────────────────────────────────────────────────────
# Runner — start_container
# ─────────────────────────────────────────────────────────────────────────────

class TestStartContainer:
    """Tests for runner.start_container — verifies it calls the Docker SDK correctly."""

    @patch("app.runner.docker.from_env")
    def test_calls_containers_run_with_correct_args(self, mock_from_env):
        """start_container should pass image, command, volumes to docker.run()."""
        from app.runner import start_container

        mock_client = MagicMock()
        mock_from_env.return_value = mock_client
        mock_container = MagicMock()
        mock_container.id = "fake_container_id_12345"
        mock_client.containers.run.return_value = mock_container

        image = "tec-suite:latest"
        command = ["-r", "/data/rinex", "-o", "/app/out", "-j", "4"]
        volumes = {"/host/rinex": {"bind": "/data/rinex", "mode": "ro"}}

        container_id = start_container(image, command, volumes)

        mock_client.containers.run.assert_called_once_with(
            image=image,
            command=command,
            volumes=volumes,
            detach=True,
            remove=False,
            labels={},
            security_opt=["no-new-privileges:true"],
        )
        assert container_id == "fake_container_id_12345"

    @patch("app.runner.docker.from_env")
    def test_enables_remove_when_auto_remove_true(self, mock_from_env):
        """start_container(auto_remove=True) should map to Docker --rm."""
        from app.runner import start_container

        mock_client = MagicMock()
        mock_from_env.return_value = mock_client
        mock_container = MagicMock()
        mock_container.id = "fake_container_id_67890"
        mock_client.containers.run.return_value = mock_container

        start_container("tec-suite:latest", ["-j", "4"], {}, auto_remove=True)

        mock_client.containers.run.assert_called_once_with(
            image="tec-suite:latest",
            command=["-j", "4"],
            volumes={},
            detach=True,
            remove=True,
            labels={},
            security_opt=["no-new-privileges:true"],
        )

    @patch("app.runner.docker.from_env")
    def test_propagates_docker_exception(self, mock_from_env):
        """If Docker raises, the exception should propagate to the caller."""
        import docker.errors
        from app.runner import start_container

        mock_client = MagicMock()
        mock_from_env.return_value = mock_client
        mock_client.containers.run.side_effect = docker.errors.ImageNotFound("tec-suite not found")

        with pytest.raises(docker.errors.ImageNotFound):
            start_container("tec-suite", [], {})


# ─────────────────────────────────────────────────────────────────────────────
# Runner — stop_container
# ─────────────────────────────────────────────────────────────────────────────

class TestStopContainer:
    """Tests for runner.stop_container."""

    @patch("app.runner.docker.from_env")
    def test_stops_running_container(self, mock_from_env):
        from app.runner import stop_container

        mock_client = MagicMock()
        mock_from_env.return_value = mock_client
        mock_container = MagicMock()
        mock_client.containers.get.return_value = mock_container

        stop_container("abc123")
        mock_container.stop.assert_called_once_with(timeout=10)

    @patch("app.runner.docker.from_env")
    def test_silently_ignores_missing_container(self, mock_from_env):
        """Stopping an already-removed container should not raise."""
        import docker.errors
        from app.runner import stop_container

        mock_client = MagicMock()
        mock_from_env.return_value = mock_client
        mock_client.containers.get.side_effect = docker.errors.NotFound("gone")

        # Should not raise
        stop_container("gone123")


# ─────────────────────────────────────────────────────────────────────────────
# Runner — stream_logs
# ─────────────────────────────────────────────────────────────────────────────

class TestStreamLogs:
    """Tests for runner.stream_logs."""

    @pytest.mark.asyncio
    @patch("app.runner._get_exit_code_only", return_value=0)
    @patch("app.runner.docker.from_env")
    async def test_reads_both_stdout_and_stderr(self, mock_from_env, mock_exit_code):
        from app.runner import stream_logs

        mock_client = MagicMock()
        mock_from_env.return_value = mock_client

        mock_container = MagicMock()
        mock_container.logs.return_value = [b"line one\n"]
        mock_client.containers.get.return_value = mock_container

        events = []
        async for event_type, payload in stream_logs("abc123", []):
            events.append((event_type, payload))
            if event_type == "done":
                break

        mock_container.logs.assert_called_once_with(
            stream=True,
            follow=True,
            stdout=True,
            stderr=True,
            tail="all",
            timestamps=False,
        )
        assert ("log", "line one") in events
        assert ("done", 0) in events

    @pytest.mark.asyncio
    @patch("app.runner._get_exit_code_only", return_value=0)
    @patch("app.runner.docker.from_env")
    async def test_emits_all_logs_and_parses_progress_independently(self, mock_from_env, mock_exit_code):
        from app.runner import stream_logs

        mock_client = MagicMock()
        mock_from_env.return_value = mock_client

        mock_container = MagicMock()
        mock_container.logs.return_value = [
            b"Unzipping /data/rinex/01/spas001q40.zip -> /data/rinex/01/spas001q40\n",
            b"Completed 101/132: spas0010.zip\n",
            b"Deleted temporary config /tmp/tmpabc.cfg\n",
            b"Completed 102/132: spas001q40.zip\n",
        ]
        mock_client.containers.get.return_value = mock_container

        patterns = [r"Completed.*?(\d+)\s*/\s*(\d+):\s+([\w.]+)"]
        events = []
        async for event_type, payload in stream_logs("abc123", patterns):
            events.append((event_type, payload))
            if event_type == "done":
                break

        log_events = [payload for event_type, payload in events if event_type == "log"]
        progress_events = [payload for event_type, payload in events if event_type == "progress"]
        assert len(log_events) == 4
        assert any("Completed 101/132" in msg for msg in log_events)
        assert any("Completed 102/132" in msg for msg in log_events)
        assert any("Unzipping" in msg for msg in log_events)
        assert any("Deleted temporary config" in msg for msg in log_events)
        assert progress_events == [76, 77]
