"""
tec_map_parallel.py — run the heavy parts of a TEC map animation in worker
processes: loading and levelling each day, and drawing each frame.

Processes, not threads: both steps are mostly pandas and matplotlib work that
holds the GIL. Loading a 19-station week with 7 threads took 3x *longer* than
one thread did, while 7 processes took 4.6x less time.

Workers are spawned rather than forked: the web process runs threads, and a
forked copy of a threaded process can deadlock on a lock some other thread
held. This module and the modules its workers import stay free of the web
app (config, database, auth), so a worker starts in a couple of seconds.
"""

from __future__ import annotations

import multiprocessing
import os
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import pandas as pd

from app.tec_map_pipeline import DaySummary, TecMapConfig, day_frame_summary

_SPAWN = multiprocessing.get_context("spawn")

# Workers inherit this environment: one BLAS/OpenMP thread each (the Docker
# image sets it for every process). At numpy's default of a thread per CPU,
# 12 workers ran 12 x 32 threads on 12 CPUs and a week took 7x longer.
for _variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_variable, "1")

# progress(stage, done, total), e.g. ("loading", 3, 7) or ("drawing", 200, 669)
Progress = Callable[[str, int, int], None]


def process_pool(
    max_workers: int,
    initializer: Callable[..., None] | None = None,
    initargs: tuple[Any, ...] = (),
) -> ProcessPoolExecutor:
    return ProcessPoolExecutor(max_workers=max_workers, mp_context=_SPAWN, initializer=initializer, initargs=initargs)


def load_day_summaries(
    *,
    root: Path,
    stations: list[str],
    pipeline: TecMapConfig,
    days: Sequence[tuple[int, int, pd.Timestamp, pd.Timestamp]],
    workers: int,
    on_day: Callable[[DaySummary], None],
    day_fn: Callable[..., DaySummary] = day_frame_summary,
) -> list[DaySummary]:
    """
    Build each day's frame summary in its own process, `workers` at a time.
    `days` holds (year, doy, segment_start, segment_end); `on_day` runs in
    this process as each day finishes. Results come back in `days` order.

    Each day in flight holds that day's raw samples, about 1.3 GB for 19
    stations, so `workers` bounds memory as well as CPU.
    """
    pool = process_pool(max_workers=max(1, min(workers, len(days))))
    try:
        futures = {
            pool.submit(
                day_fn,
                root=root,
                year=year,
                doy=doy,
                stations=stations,
                start_time=start.isoformat(sep=" "),
                end_time=end.isoformat(sep=" "),
                pipeline=pipeline,
            ): index
            for index, (year, doy, start, end) in enumerate(days)
        }
        results: list[DaySummary | None] = [None] * len(days)
        for future in as_completed(futures):
            result = future.result()
            results[futures[future]] = result
            on_day(result)
    except BaseException:
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    pool.shutdown()
    return [result for result in results if result is not None]
