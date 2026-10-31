"""Tests for scan progress reporting.

A full RINEX walk can take minutes, so the indexer reports which scans are
running and how long the previous scan of the same root took, letting the hub
tell users how long to wait.
"""

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("DATA_INDEXER_WATCHERS_ENABLED", "false")

import data_indexer as di


class ScanProgressTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patcher = patch.object(di, "_CACHE_DB_PATH", os.path.join(self._tmp.name, "cache.db"))
        patcher.start()
        self.addCleanup(patcher.stop)
        di._init_cache_db()

    def _blocked_scan(self, root: str) -> tuple[threading.Event, threading.Thread]:
        started = threading.Event()
        release = threading.Event()

        def scan(_path: Path) -> list:
            started.set()
            release.wait(5)
            return []

        thread = threading.Thread(target=di._scan_and_store, args=("rinex", root, Path(root), scan, {}))
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(release.set)
        assert started.wait(5), "Scan thread did not start in time"
        return release, thread

    def test_reports_a_running_scan_with_its_type_and_root(self) -> None:
        self._blocked_scan("/mnt/rinex-yrsdq")
        assert [(s["type"], s["root"]) for s in di.scan_progress()] == [("rinex", "/mnt/rinex-yrsdq")], (
            "Running scan was not reported"
        )

    def test_drops_a_scan_once_it_finishes(self) -> None:
        release, thread = self._blocked_scan("/mnt/rinex-kpwe")
        release.set()
        thread.join(5)
        assert di.scan_progress() == [], "Finished scan was still reported as running"

    def test_drops_a_scan_that_fails(self) -> None:
        def scan(_path: Path) -> list:
            raise OSError("disk vanished")

        with pytest.raises(OSError, match="disk vanished"):
            di._scan_and_store("abstec", "/mnt/abstec-qzt", Path("/mnt/abstec-qzt"), scan, {})
        assert di.scan_progress() == [], "Failed scan was still reported as running"

    def test_expects_the_duration_of_the_previous_scan_of_that_root(self) -> None:
        with patch.object(di, "_clock", side_effect=[100.0, 237.5]):
            di._scan_and_store("rinex", "/mnt/rinex-hvb", Path("/mnt/rinex-hvb"), lambda _p: [], {})
        self._blocked_scan("/mnt/rinex-hvb")
        assert di.scan_progress()[0]["expected_seconds"] == 137.5, "Previous scan duration was not offered as estimate"

    def test_has_no_estimate_for_a_root_never_scanned(self) -> None:
        self._blocked_scan("/mnt/rinex-nowe")
        assert di.scan_progress()[0]["expected_seconds"] is None, "Estimate was invented for a root never scanned"


if __name__ == "__main__":
    unittest.main()
