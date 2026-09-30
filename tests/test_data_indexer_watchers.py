import importlib.util
import sys
import time
import uuid
from pathlib import Path

import pytest

DATA_INDEXER_DIR = Path(__file__).resolve().parents[1] / "data-indexer"


def _load_data_indexer_module(monkeypatch):
    module_path = DATA_INDEXER_DIR / "data_indexer.py"
    spec = importlib.util.spec_from_file_location(f"data_indexer_test_{uuid.uuid4().hex}", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None

    monkeypatch.setenv("DATA_INDEXER_CACHE_DB_PATH", ":memory:")
    spec.loader.exec_module(module)
    return module


class FakePath:
    def __init__(self, raw_path: str):
        self.raw_path = raw_path

    def exists(self):
        return True

    def is_dir(self):
        return True

    def __truediv__(self, other):
        return FakeMissingPath(f"{self.raw_path}/{other}")


class FakeMissingPath(FakePath):
    def is_dir(self):
        return False


@pytest.fixture
def indexer(monkeypatch):
    module = _load_data_indexer_module(monkeypatch)
    monkeypatch.setattr(module, "Path", FakePath)
    monkeypatch.setattr(module, "_CACHE_TTL_SEC", 3600.0)
    monkeypatch.setattr(module, "_ensure_watcher", lambda host_root, root_path: None)
    monkeypatch.setattr(module, "_save_cache_to_db", lambda *args, **kwargs: None)
    for cache in (module._rinex_cache, module._parquet_cache, module._parquet_sat_cache, module._tecsuite_cache):
        cache.clear()
    module._root_generation.clear()
    module._scanned_generation.clear()
    return module


def test_rinex_cache_refreshes_immediately_when_invalidated(indexer, monkeypatch):
    host_root = "/virtual/rinex"
    old_result = [{"year": "2025_original", "days": [{"day": "001", "stations": 1}]}]
    new_result = [{"year": "2025_original", "days": [{"day": "001", "stations": 2}]}]
    monkeypatch.setattr(indexer, "_scan_rinex", lambda root: new_result)

    indexer._rinex_cache[host_root] = (time.time(), old_result)
    indexer._root_generation[host_root] = 1  # a filesystem event arrived

    result = indexer.list_rinex_server_structure(host_root)

    assert result == new_result
    assert indexer._rinex_cache[host_root][1] == new_result
    assert not indexer._cache_is_invalidated("rinex", host_root)


def test_one_change_invalidates_every_cache_type_of_that_root(indexer, monkeypatch):
    """/parquet and /parquet-satellites share a root; the first call must not consume the change."""
    host_root = "/virtual/parquet"
    monkeypatch.setattr(indexer, "_scan_parquet", lambda root: ["new-parquet"])
    monkeypatch.setattr(indexer, "_scan_parquet_satellites_parallel", lambda root: ["new-sat"])
    now = time.time()
    indexer._parquet_cache[host_root] = (now, ["old-parquet"])
    indexer._parquet_sat_cache[host_root] = (now, ["old-sat"])
    indexer._root_generation[host_root] = 1

    assert indexer.list_parquet_output_structure(host_root) == ["new-parquet"]
    assert indexer.list_parquet_satellite_structure(host_root) == ["new-sat"]


def test_change_during_scan_triggers_another_scan(indexer, monkeypatch):
    host_root = "/virtual/parquet"
    scans = []

    def _scan(root):
        scans.append(len(scans))
        if len(scans) == 1:
            # A file lands while the first scan is running.
            indexer._root_generation[host_root] += 1
        return [f"scan-{len(scans)}"]

    monkeypatch.setattr(indexer, "_scan_parquet", _scan)
    indexer._parquet_cache[host_root] = (time.time(), ["old"])
    indexer._root_generation[host_root] = 1

    assert indexer.list_parquet_output_structure(host_root) == ["scan-1"]
    assert indexer._cache_is_invalidated("parquet", host_root)
    assert indexer.list_parquet_output_structure(host_root) == ["scan-2"]
    assert not indexer._cache_is_invalidated("parquet", host_root)


def test_background_refresh_is_tracked_per_cache_type(indexer, monkeypatch):
    started = []

    class _Thread:
        def __init__(self, target, daemon):
            started.append(target)

        def start(self):
            pass

    monkeypatch.setattr(indexer.threading, "Thread", _Thread)

    indexer._trigger_background_refresh("parquet", "/r", FakePath("/r"), lambda root: [], {})
    indexer._trigger_background_refresh("parquet_sat", "/r", FakePath("/r"), lambda root: [], {})
    indexer._trigger_background_refresh("parquet", "/r", FakePath("/r"), lambda root: [], {})

    assert len(started) == 2


def test_watcher_ignores_non_structural_events(monkeypatch):
    pytest.importorskip("watchdog")
    module = _load_data_indexer_module(monkeypatch)

    class _Event:
        def __init__(self, event_type):
            self.event_type = event_type
            self.src_path = "/r/2026/001/file"

    handler = module._RootChangeHandler("/r")
    for event_type in ("opened", "closed_no_write", "closed", "modified"):
        handler.on_any_event(_Event(event_type))
    assert module._root_generation.get("/r", 0) == 0

    for event_type in ("created", "deleted", "moved"):
        handler.on_any_event(_Event(event_type))
    assert module._root_generation["/r"] == 3


@pytest.fixture
def indexer_app(monkeypatch):
    """data-indexer/app.py with its own top-level modules on sys.path."""
    pytest.importorskip("dicttoxml")
    monkeypatch.setenv("DATA_INDEXER_CACHE_DB_PATH", ":memory:")
    monkeypatch.setenv("DATA_INDEXER_WATCHERS_ENABLED", "false")
    monkeypatch.setenv("PARQUET_OUTPUT_TECSUITE_DATA_PATH_CONTAINER", "/mnt/tecsuite-parquet-out")
    monkeypatch.syspath_prepend(str(DATA_INDEXER_DIR))
    # The hub also has a top-level "app" package; load the indexer's app.py by path.
    for name in ("data_indexer", "rinex_station_map"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    spec = importlib.util.spec_from_file_location(f"data_indexer_app_{uuid.uuid4().hex}", DATA_INDEXER_DIR / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_indexer_rejects_roots_outside_configured_paths(indexer_app, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(indexer_app, "list_parquet_output_structure", lambda root, refresh=False: [])
    client = TestClient(indexer_app.app)
    for bad_root in ("/etc", "/mnt/tecsuite-parquet-out/../../etc", "/mnt/tecsuite-parquet-outside"):
        assert client.get("/parquet", params={"root": bad_root}).status_code == 400, bad_root
    for good_root in ("/mnt/tecsuite-parquet-out", "/mnt/tecsuite-parquet-out/2026"):
        assert client.get("/parquet", params={"root": good_root}).status_code == 200, good_root


@pytest.mark.parametrize("mode", ["true", "async"])
def test_startup_indexing_runs_off_the_event_loop(indexer_app, monkeypatch, mode):
    """The warm-up walks whole data trees; /health must keep answering meanwhile."""
    import threading

    from fastapi.testclient import TestClient

    import data_indexer

    monkeypatch.setenv("DATA_INDEXER_RUN_ON_STARTUP", mode)
    monkeypatch.setattr(data_indexer, "should_run_full_index", lambda: (True, "test", None))
    monkeypatch.setattr(data_indexer, "set_last_full_index_time", lambda: None)
    release = threading.Event()
    started = threading.Event()

    def _slow_scan(root, refresh=False):
        started.set()
        release.wait(timeout=10)
        return []

    monkeypatch.setattr(indexer_app, "list_rinex_server_structure", _slow_scan)
    began = time.monotonic()
    try:
        # The scan is held open until the end, so everything below, including
        # the client's own startup, must complete while it is still running.
        with TestClient(indexer_app.app) as client:
            assert started.wait(timeout=5), "startup indexing did not run"
            assert client.get("/health").status_code == 200
            assert time.monotonic() - began < 3, "the server waited for the startup scan"
    finally:
        release.set()
