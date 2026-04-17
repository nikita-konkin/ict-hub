import importlib.util
import uuid
from pathlib import Path


def _load_data_indexer_module(monkeypatch):
    module_path = Path(__file__).resolve().parents[1] / "data-indexer" / "data_indexer.py"
    spec = importlib.util.spec_from_file_location(f"data_indexer_test_{uuid.uuid4().hex}", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None

    monkeypatch.setenv("DATA_INDEXER_CACHE_DB_PATH", ":memory:")
    spec.loader.exec_module(module)
    return module


def test_rinex_cache_refreshes_immediately_when_invalidated(monkeypatch):
    module = _load_data_indexer_module(monkeypatch)

    class FakePath:
        def __init__(self, raw_path: str):
            self.raw_path = raw_path

        def exists(self):
            return True

        def is_dir(self):
            return True

    host_root = "/virtual/rinex"
    old_result = [{"year": "2025_original", "days": [{"day": "001", "stations": 1}]}]
    new_result = [{"year": "2025_original", "days": [{"day": "001", "stations": 2}]}]

    monkeypatch.setattr(module, "Path", FakePath)
    monkeypatch.setattr(module, "_CACHE_TTL_SEC", 3600.0)
    monkeypatch.setattr(module, "_ensure_watcher", lambda host_root, root_path: None)
    monkeypatch.setattr(module, "_scan_rinex", lambda root: new_result)
    monkeypatch.setattr(module, "_save_cache_to_db", lambda *args, **kwargs: None)

    module._rinex_cache.clear()
    module._cache_invalidated.clear()
    module._rinex_cache[host_root] = (module.time.monotonic(), old_result)
    module._cache_invalidated[host_root] = True

    result = module.list_rinex_server_structure(host_root)

    assert result == new_result
    assert module._rinex_cache[host_root][1] == new_result
    assert module._cache_invalidated[host_root] is False
