"""Experimental native AbsolTEC batch reconstruction.

This independent implementation has known differences from the legacy program;
scientific parity is unverified. Inputs are tec-suite DAT satellite files under
ROOT/YYYY/DDD/SITE. Outputs are isolated per station/day and never run a Windows
executable. Compare paired results before using this implementation in research.
"""

from __future__ import annotations

import argparse
import calendar
import concurrent.futures
import contextlib
import hashlib
import json
import math
import os
import re
import shutil
import sys
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from .bias import write_dcb_output
from .io import read_dat, write_tec_output

_SITE = re.compile(r"[A-Za-z0-9]{4}[A-Za-z0-9_.-]{0,76}")
_FILE_ID = re.compile(
    r"[A-Za-z0-9]{4}_(?P<satellite>[A-Za-z]\d{2,3})_\d{3}_(?P<year>\d{2}|\d{4})(?:__dup\d+)?\.dat", re.IGNORECASE
)


def reconstruct(data, config):
    # Delayed import keeps --help usable without starting the numerical stack.
    from .solver import reconstruct as solve

    return solve(data, config)


def _build_config(args):
    from .solver import SolverConfig

    return SolverConfig(
        time_step_hours=args.time_step_hours,
        elevation_cutoff=args.elevation_cutoff,
        correction_coefficient=args.correction_coefficient,
        model=args.model,
        window_hours=args.window_hours,
        min_arc_samples=args.min_arc_samples,
        min_window_samples=args.min_window_samples,
        min_satellites=args.min_satellites,
        auto_mapping_alpha=args.auto_alpha,
        mapping_shell_height_km=args.shell_height_km,
        nonneg=args.nonneg,
    )


@dataclass(frozen=True)
class StationTask:
    source: Path
    output_root: Path
    year: int
    day: int
    site: str
    config: Any
    overwrite: bool


def _json_file(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _identity(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    return {"path": str(path), "sha256": digest.hexdigest(), "bytes": size}


def _owned_cleanup(path: Path, parent: Path) -> None:
    # Only temporary directories created by this transaction are removed.
    if path.parent != parent or not path.name.startswith(".") or path.is_symlink():
        raise ValueError("Refusing to remove an unexpected transaction directory")
    if path.resolve().parent != parent.resolve():
        raise ValueError("Transaction directory resolves outside its output parent")
    shutil.rmtree(path)


def _failure_record(task: StationTask, report: dict[str, Any]) -> Path:
    parent = task.output_root / "_failures" / str(task.year) / f"{task.day:03d}"
    if not parent.resolve().is_relative_to(task.output_root):
        raise ValueError("Failure output directory resolves outside the output root")
    parent.mkdir(parents=True, exist_ok=True)
    if not parent.resolve().is_relative_to(task.output_root):
        raise ValueError("Failure output directory resolves outside the output root")
    path = parent / f"{task.site}-{report['run_id']}.json"
    _json_file(path, report)
    return path


def _run_station(task: StationTask) -> dict[str, Any]:
    started = time.perf_counter()
    run_id = uuid.uuid4().hex
    report: dict[str, Any] = {
        "schema_version": 1,
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "year": task.year,
        "day_of_year": task.day,
        "site": task.site,
        "scientific_parity": "unverified",
        "config": asdict(task.config),
        "inputs": [],
        "excluded_unsupported_constellations": {"files": 0, "observations": 0, "satellites": []},
    }
    parent = task.output_root / str(task.year) / f"{task.day:03d}"
    destination = parent / task.site
    lock = parent / f".{task.site}.lock"
    locked = False
    stage = backup = None
    try:
        if not parent.resolve().is_relative_to(task.output_root):
            raise ValueError("Station output parent resolves outside the output root")
        parent.mkdir(parents=True, exist_ok=True)
        if not parent.resolve().is_relative_to(task.output_root):
            raise ValueError("Station output parent resolves outside the output root")
        if destination.is_symlink():
            raise ValueError("Station output cannot be a symbolic link")
        if destination.exists() and not destination.is_dir():
            raise ValueError("Existing station output must be a directory")
        lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        locked = True
        with os.fdopen(lock_fd, "w", encoding="ascii") as stream:
            stream.write(run_id)
        if destination.exists() and not task.overwrite:
            raise FileExistsError(f"Output already exists: {destination}; use --overwrite to replace it")
        if not task.source.is_dir():
            raise ValueError(f"Input station directory does not exist: {task.source}")
        if not task.source.resolve().is_relative_to(task.source.parents[2].resolve()):
            raise ValueError("Input station directory resolves outside its input root")
        files = sorted(
            (path for path in task.source.iterdir() if path.suffix.lower() == ".dat"),
            key=lambda item: item.name.casefold(),
        )
        if not files:
            raise ValueError(f"No DAT files in {task.source}")
        observations = []
        for path in files:
            if not path.resolve().is_relative_to(task.source.resolve()):
                raise ValueError(f"Input DAT resolves outside its station directory: {path}")
            identity = _identity(path)
            report["inputs"].append(identity)
            name = _FILE_ID.fullmatch(path.name)
            if name and name["satellite"][0].upper() not in {"G", "R"}:
                with path.open(encoding="latin-1") as stream:
                    record_count = sum(
                        bool(text := line.strip()) and not text.startswith("#") and not text.lower().startswith("tsn")
                        for line in stream
                    )
                excluded = report["excluded_unsupported_constellations"]
                excluded["files"] += 1
                excluded["observations"] += record_count
                excluded["satellites"].append(name["satellite"].upper())
                identity["excluded"] = "unsupported constellation; record count does not validate observations"
                if _identity(path)["sha256"] != identity["sha256"]:
                    raise ValueError(f"Input changed while being counted: {path}")
                continue
            data = read_dat(path)
            if _identity(path) != identity:
                raise ValueError(f"Input changed while being read: {path}")
            identity["satellite_id"] = data.satellite_id
            identity["observations"] = len(data.hour)
            identity["read_diagnostics"] = asdict(data.diagnostics)
            if data.station.casefold() != task.site[:4].casefold():
                raise ValueError(f"Input station {data.station} disagrees with selected directory {task.site}")
            if data.day_of_year is not None and data.day_of_year != task.day:
                raise ValueError(f"Input day disagrees with selected day: {path}")
            if data.year is not None and data.year != task.year:
                raise ValueError(f"Input year disagrees with selected year: {path}")
            if name and int(name["year"]) != (task.year % 100 if len(name["year"]) == 2 else task.year):
                raise ValueError(f"Input filename year disagrees with selected year: {path}")
            if data.satellite_id[0] not in {"G", "R"}:
                excluded = report["excluded_unsupported_constellations"]
                excluded["files"] += 1
                excluded["observations"] += len(data.hour)
                excluded["satellites"].append(data.satellite_id)
                continue
            observations.append(data)
        if not observations:
            raise ValueError("No GPS or GLONASS satellite files remain after constellation filtering")
        solution = reconstruct(observations, task.config)
        rows = np.asarray(solution.rows, dtype=float)
        if rows.ndim != 2 or rows.shape[1] != 8 or len(rows) == 0:
            raise ValueError("Solver returned no eight-column TEC results")
        solved = np.isfinite(rows[:, 1:]).all(axis=1)
        report.update(
            status="success" if bool(solved.all()) else "partial",
            solved_epochs=int(solved.sum()),
            total_epochs=len(rows),
            diagnostics=solution.diagnostics,
            runtime_seconds=time.perf_counter() - started,
        )
        stage = Path(tempfile.mkdtemp(prefix=f".{task.site}.stage-", dir=parent))
        basename = f"{task.site[:4].lower()}_{task.day:03d}_{task.year}"
        write_tec_output(stage / f"{basename}.dat", rows)
        write_dcb_output(stage / f"DCB_{basename}.dat", solution.combined_bias)
        _json_file(
            stage / f"DCB_{basename}.json",
            {
                "description": "Estimated combined satellite/receiver link biases; not separate legacy DCB values.",
                "units": "TECU",
                "scientific_parity": "unverified",
                "combined_bias": solution.combined_bias,
            },
        )
        _json_file(stage / "diagnostics.json", report)
        if destination.exists():
            if not task.overwrite:
                raise FileExistsError(f"Output appeared during computation: {destination}")
            backup = parent / f".{task.site}.previous-{run_id}"
            os.replace(destination, backup)
        try:
            os.replace(stage, destination)
            stage = None
        except OSError:
            if backup is not None:
                os.replace(backup, destination)
                backup = None
            raise
        if backup is not None:
            try:
                _owned_cleanup(backup, parent)
                backup = None
            except OSError as exc:
                report["cleanup_warning"] = f"Previous output retained at {backup}: {exc}"
        report["output"] = str(destination)
    except Exception as exc:
        report.update(status="failed", error=str(exc), runtime_seconds=time.perf_counter() - started)
        if backup is not None and backup.exists():
            report["retained_previous_output"] = str(backup)
        try:
            report["failure_diagnostics"] = str(_failure_record(task, report))
        except (OSError, ValueError, TypeError) as diagnostic_error:
            report["diagnostics_error"] = str(diagnostic_error)
    finally:
        if stage is not None:
            try:
                _owned_cleanup(stage, parent)
            except (OSError, ValueError) as exc:
                report["cleanup_warning"] = str(exc)
        if locked:
            try:
                lock.unlink(missing_ok=True)
            except OSError as exc:
                report["cleanup_warning"] = f"Could not remove transaction lock {lock}: {exc}"
    return report


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise argparse.ArgumentTypeError("must be finite")
    return parsed


def _days(text: str, year: int) -> list[int]:
    result: set[int] = set()
    for token in text.split(","):
        if re.fullmatch(r"\d{1,3}", token.strip()):
            result.add(int(token))
        elif re.fullmatch(r"\d{1,3}-\d{1,3}", token.strip()):
            start, end = (int(part) for part in token.strip().split("-"))
            if start > end:
                raise ValueError(f"Reversed day range: {token}")
            result.update(range(start, end + 1))
        else:
            raise ValueError(f"Invalid day selection: {token!r}")
    if not result or min(result) < 1 or max(result) > (366 if calendar.isleap(year) else 365):
        raise ValueError(f"Day selection is outside year {year}")
    return sorted(result)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dat-path", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--year", required=True, type=int)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--day-of-year", type=_positive_int)
    selection.add_argument("--days", help="comma-separated days or inclusive ranges, e.g. 001,003-005")
    parser.add_argument("--site", help="comma-separated station directory names; omitted discovers all stations")
    parser.add_argument("--time-step-hours", type=_finite_float, default=0.5)
    parser.add_argument("--elevation-cutoff", type=_finite_float, default=10.0)
    parser.add_argument("--correction-coefficient", type=_finite_float, default=0.97)
    parser.add_argument("--window-hours", type=_finite_float, default=1.0)
    parser.add_argument("--min-arc-samples", type=_positive_int, default=21)
    parser.add_argument("--min-window-samples", type=_positive_int, default=20)
    parser.add_argument("--min-satellites", type=_positive_int, default=6)
    parser.add_argument("--model", choices=("legacy-2017", "published"), default="legacy-2017")
    parser.add_argument(
        "--auto-alpha",
        action="store_true",
        help="published model: pick the TuRBOTEC latitude-dependent MSLM coefficient "
        "(0.87 low / 0.97 mid / 0.94 high latitude) instead of --correction-coefficient",
    )
    parser.add_argument(
        "--shell-height-km",
        type=_finite_float,
        default=450.0,
        help="published model: ionospheric shell height for the mapping function (default 450)",
    )
    parser.add_argument(
        "--nonneg",
        action="store_true",
        help="published model: bounded-variable least squares with I_V >= 0.5 TECU and "
        "slant-TEC-preserving DCB bounds (TuRBOTEC Eq. 8)",
    )
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--jobs", type=_positive_int, default=1, help="simultaneous station computations, 1-8")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if not 1000 <= args.year <= 9999 or args.jobs > 8:
            raise ValueError("Year must have four digits and jobs must be between 1 and 8")
        if not 0 < args.time_step_hours <= 24 or not 0 < args.window_hours <= 6:
            raise ValueError("Time step must be in (0,24] hours and window half-width in (0,6] hours")
        if not 0 <= args.elevation_cutoff < 90 or not 0 < args.correction_coefficient <= 1:
            raise ValueError("Elevation cutoff must be in [0,90) and correction coefficient in (0,1]")
        days = _days(args.days or str(args.day_of_year), args.year)
        source_root = args.dat_path.resolve()
        output_root = args.output_dir.resolve()
        if not source_root.is_dir():
            raise ValueError(f"Input root does not exist: {source_root}")
        if source_root.is_relative_to(output_root) or output_root.is_relative_to(source_root):
            raise ValueError("Input and output roots must not overlap")
        if output_root.exists() and not output_root.is_dir():
            raise ValueError("Output root must be a directory")
        sites = list(dict.fromkeys(part.strip() for part in args.site.split(","))) if args.site else None
        if sites is not None and any(not _SITE.fullmatch(site) for site in sites):
            raise ValueError("Station names must start with four letters/digits and contain no path separators")
        config = _build_config(args)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    output_root.mkdir(parents=True, exist_ok=True)
    tasks = []
    discovery_failures = 0
    for day in days:
        day_root = source_root / str(args.year) / f"{day:03d}"
        day_sites = sites
        if day_sites is None:
            day_sites = (
                sorted(
                    (path.name for path in day_root.iterdir() if path.is_dir() and _SITE.fullmatch(path.name)),
                    key=str.casefold,
                )
                if day_root.is_dir()
                else []
            )
        if not day_sites:
            discovery_failures += 1
            task = StationTask(day_root, output_root, args.year, day, "all-stations", config, args.overwrite)
            report = {
                "schema_version": 1,
                "run_id": uuid.uuid4().hex,
                "created_at": datetime.now(UTC).isoformat(),
                "year": args.year,
                "day_of_year": day,
                "site": "all-stations",
                "status": "failed",
                "scientific_parity": "unverified",
                "config": asdict(config),
                "inputs": [],
                "runtime_seconds": 0.0,
                "error": f"No station directories for year={args.year} day={day:03d}",
            }
            try:
                _failure_record(task, report)
            except (OSError, ValueError) as exc:
                print(f"Could not write failure diagnostics: {exc}", file=sys.stderr)
            print(report["error"], file=sys.stderr)
        for site in day_sites:
            tasks.append(StationTask(day_root / site, output_root, args.year, day, site, config, args.overwrite))
    statuses = {"success": 0, "partial": 0, "failed": discovery_failures}

    def completed(report):
        statuses[report["status"]] += 1
        if report["status"] == "failed":
            print(
                f"Error year={report['year']} day={report['day_of_year']:03d} site={report['site']}: {report['error']}",
                file=sys.stderr,
            )
        elif report["status"] == "partial":
            print(
                f"Partial site={report['site']}: solved {report['solved_epochs']} / {report['total_epochs']} epochs",
                file=sys.stderr,
            )
        if report.get("cleanup_warning"):
            print(report["cleanup_warning"], file=sys.stderr)
        count = sum(statuses.values()) - discovery_failures
        print(f"Completed {count} / {len(tasks)}", flush=True)

    with contextlib.ExitStack() as stack:
        executor = (
            stack.enter_context(concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs)) if args.jobs > 1 else None
        )
        futures = []
        for index, task in enumerate(tasks, 1):
            print(f"Processing year={task.year} day={task.day:03d} site={task.site} ({index}/{len(tasks)})", flush=True)
            if executor is None:
                completed(_run_station(task))
            else:
                futures.append(executor.submit(_run_station, task))
        for future in concurrent.futures.as_completed(futures):
            completed(future.result())
    print(f"Finished: {statuses['success']} complete, {statuses['partial']} partial, {statuses['failed']} failed.")
    return 1 if statuses["partial"] or statuses["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
