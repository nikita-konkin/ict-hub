"""Tests for the startup re-index gate.

Restarting the service must not re-walk the whole RINEX tree when it was just
indexed, so `should_run_full_index` refuses while the previous index is younger
than DATA_INDEXER_MIN_REINDEX_INTERVAL_SEC.
"""

import os
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("DATA_INDEXER_WATCHERS_ENABLED", "false")

import data_indexer as di


class ReindexGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        db_path = os.path.join(self._tmp.name, "cache.db")
        patcher = patch.object(di, "_CACHE_DB_PATH", db_path)
        patcher.start()
        self.addCleanup(patcher.stop)
        di._init_cache_db()

    def _set_interval(self, seconds: float) -> None:
        patcher = patch.object(di, "_MIN_REINDEX_INTERVAL_SEC", seconds)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_runs_when_nothing_was_ever_indexed(self) -> None:
        self._set_interval(86400)

        allowed, reason, age = di.should_run_full_index()

        self.assertTrue(allowed)
        self.assertIn("no previous index", reason)
        self.assertIsNone(age)

    def test_refuses_immediately_after_an_index(self) -> None:
        # The restart case: indexing just finished, service comes back up.
        self._set_interval(86400)
        di.set_last_full_index_time()

        allowed, reason, _ = di.should_run_full_index()

        self.assertFalse(allowed)
        self.assertIn("minimum re-index interval", reason)

    def test_refuses_just_below_the_interval(self) -> None:
        self._set_interval(86400)
        di.set_last_full_index_time(time.time() - 86000)

        allowed, _, _ = di.should_run_full_index()

        self.assertFalse(allowed)

    def test_runs_once_the_index_is_stale(self) -> None:
        self._set_interval(86400)
        di.set_last_full_index_time(time.time() - 86400 - 60)

        allowed, _, age = di.should_run_full_index()

        self.assertTrue(allowed)
        self.assertGreater(age, 86400)

    def test_zero_interval_disables_the_check(self) -> None:
        self._set_interval(0)
        di.set_last_full_index_time()

        allowed, reason, _ = di.should_run_full_index()

        self.assertTrue(allowed)
        self.assertIn("disabled", reason)

    def test_future_timestamp_does_not_block_forever(self) -> None:
        # Host clock sync can move time backwards; a marker "in the future"
        # must not lock indexing out indefinitely.
        self._set_interval(86400)
        di.set_last_full_index_time(time.time() + 10_000)

        allowed, reason, _ = di.should_run_full_index()

        self.assertTrue(allowed)
        self.assertIn("future", reason)

    def test_marker_survives_a_restart(self) -> None:
        self._set_interval(86400)
        di.set_last_full_index_time()

        # A fresh process re-reads the same persistent database.
        di._rinex_cache.clear()
        stored = di.get_last_full_index_time()

        self.assertIsNotNone(stored)
        self.assertAlmostEqual(stored, time.time(), delta=60)

    def test_marker_is_wall_clock_not_monotonic(self) -> None:
        # time.monotonic() is measured from boot, so it cannot express "a day
        # ago" and jumps backwards on reboot. The marker must be epoch-based.
        self._set_interval(86400)
        di.set_last_full_index_time()

        stored = di.get_last_full_index_time()

        self.assertGreater(stored, 1_600_000_000)  # comfortably after 2020


if __name__ == "__main__":
    unittest.main()
