"""Reproduce native/reference comparisons in a new, isolated output directory.

No curve calibration, interpolation, old-reference reuse, or scientific accuracy
claim is made. The original executable runs only at explicitly allowed steps of
at least 0.05 hours; its default reference step is 0.5 hours. A missing executable
or skipped numerical test suite leaves acceptance incomplete, never passing.
"""

# ruff: noqa: E402 -- direct CLI bootstraps the known repository root before local imports.

from __future__ import annotations

import argparse
import calendar
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np

# A direct script invocation places scripts/, rather than the repository, on
# sys.path. Resolve our own source root, never a caller-selected import path.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from absoltec_native import __version__
from absoltec_native.bias import split_biases, write_dcb_output
from absoltec_native.io import read_dat, write_tec_output
from absoltec_native.solver import SolverConfig, reconstruct
from scripts.compare_absoltec import COEFFICIENTS, compare_files, read_results

_INPUT_NAME = re.compile(
    r"(?P<site>[A-Za-z0-9]{4})_(?P<satellite>[A-Za-z]\d{2,3})_(?P<day>\d{3})_(?P<year>\d{2}|\d{4})(?:__dup\d+)?\.dat",
    re.IGNORECASE,
)
_MODELS = {"legacy-2017", "published"}


@dataclass(frozen=True)
class ValidationSettings:
    dat_path: Path
    output_dir: Path
    year: int
    days: tuple[int, ...]
    sites: tuple[str, ...]
    steps: tuple[float, ...] = (0.5, 0.05, 1 / 60)
    models: tuple[str, ...] = ("legacy-2017", "published")
    legacy_exe: Path | None = None
    legacy_steps: tuple[float, ...] = (0.5,)
    timeout_seconds: float = 120.0
    atol: float = 0.001
    rtol: float = 0.0
    time_tolerance: float = 1e-6
    elevation_cutoff: float = 10.0
    correction_coefficient: float = 0.97
    window_hours: float = 1.0
    run_numerical_tests: bool = False
    input_provenance: Path | None = None


def _write_json(path: Path, data: Any) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(data, stream, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _file_identity(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    return {"path": str(path.resolve()), "sha256": digest.hexdigest(), "bytes": size}


def _copy_verified(source: Path, destination: Path) -> dict[str, Any]:
    identity = _file_identity(source)
    shutil.copy2(source, destination)
    if _file_identity(destination)["sha256"] != identity["sha256"]:
        raise ValueError(f"Source changed during snapshot copy: {source}")
    identity["snapshot"] = str(destination.resolve())
    return identity


def _step_name(step: float) -> str:
    return format(step, ".12g").replace(".", "p").replace("-", "m")


def _config(settings: ValidationSettings, model: str, step: float) -> SolverConfig:
    return SolverConfig(
        time_step_hours=step,
        model=model,
        elevation_cutoff=settings.elevation_cutoff,
        correction_coefficient=settings.correction_coefficient,
        window_hours=settings.window_hours,
    )


def _validate_settings(settings: ValidationSettings) -> None:
    if not 1000 <= settings.year <= 9999:
        raise ValueError("Year must have four digits")
    last_day = 366 if calendar.isleap(settings.year) else 365
    if not settings.days or any(not 1 <= day <= last_day for day in settings.days):
        raise ValueError("Day selection is outside the requested year")
    if len(set(settings.days)) != len(settings.days):
        raise ValueError("Duplicate days are not allowed")
    if not settings.sites or any(not re.fullmatch(r"[A-Za-z0-9]{4}", site) for site in settings.sites):
        raise ValueError("Sites must be four-character station directory names")
    if len({site.casefold() for site in settings.sites}) != len(settings.sites):
        raise ValueError("Duplicate station codes are not allowed")
    if not settings.steps or len(set(settings.steps)) != len(settings.steps):
        raise ValueError("Native steps must be nonempty and unique")
    if (
        not settings.models
        or not set(settings.models).issubset(_MODELS)
        or len(set(settings.models)) != len(settings.models)
    ):
        raise ValueError("Models must be unique legacy-2017 and/or published")
    if not settings.legacy_steps or len(set(settings.legacy_steps)) != len(settings.legacy_steps):
        raise ValueError("Legacy reference steps must be nonempty and unique")
    if any(not math.isfinite(step) or not 0.05 <= step <= 24 for step in settings.legacy_steps):
        raise ValueError("Legacy steps below 0.05 hours are prohibited because the executable is known to crash")
    for model in settings.models:
        for step in settings.steps:
            _config(settings, model, step)
    for name in ("atol", "rtol", "time_tolerance", "timeout_seconds"):
        value = getattr(settings, name)
        if not math.isfinite(value) or value < 0 or (name == "timeout_seconds" and value == 0):
            raise ValueError(f"{name} must be finite and {'positive' if name == 'timeout_seconds' else 'nonnegative'}")
    source = settings.dat_path.resolve()
    output = settings.output_dir.resolve()
    if not source.is_dir():
        raise ValueError(f"Input root does not exist: {source}")
    if source.is_relative_to(output) or output.is_relative_to(source):
        raise ValueError("Input and validation output roots must not overlap")
    if output.exists():
        raise ValueError(f"Validation output must be a new directory; refusing existing output: {output}")
    if settings.legacy_exe is not None and not settings.legacy_exe.is_file():
        raise ValueError(f"Legacy executable does not exist: {settings.legacy_exe}")
    if settings.input_provenance is not None and not settings.input_provenance.is_file():
        raise ValueError(f"Input provenance does not exist: {settings.input_provenance}")


def _snapshot_case(settings: ValidationSettings, case_root: Path, day: int, site: str) -> dict[str, Any]:
    source_root = settings.dat_path.resolve()
    source = source_root / str(settings.year) / f"{day:03d}" / site
    target = case_root / "input" / str(settings.year) / f"{day:03d}" / site
    manifest: dict[str, Any] = {
        "year": settings.year,
        "day_of_year": day,
        "site": site,
        "adjacent_day_context": "not_loaded",
        "files": [],
        "excluded_files": [],
        "status": "ready",
    }
    case_root.mkdir(parents=True, exist_ok=False)
    try:
        if not source.is_dir() or not source.resolve().is_relative_to(source_root):
            raise ValueError(f"Missing or external station directory: {source}")
        target.mkdir(parents=True)
        for path in sorted(source.iterdir(), key=lambda item: item.name.casefold()):
            if path.suffix.lower() != ".dat":
                continue
            if not path.is_file() or not path.resolve().is_relative_to(source_root):
                raise ValueError(f"Invalid input file path: {path}")
            name = _INPUT_NAME.fullmatch(path.name)
            if not name:
                raise ValueError(f"Unrecognized tec-suite satellite filename: {path.name}")
            if name["satellite"][0].upper() not in {"G", "R"}:
                manifest["excluded_files"].append({**_file_identity(path), "reason": "unsupported constellation"})
                continue
            data = read_dat(path)
            filename_year = int(name["year"])
            if (
                data.station.casefold() != site.casefold()
                or int(name["day"]) != day
                or filename_year != (settings.year % 100 if len(name["year"]) == 2 else settings.year)
            ):
                raise ValueError(f"Input identity disagrees with requested station/day/year: {path}")
            record = _copy_verified(path, target / path.name)
            record.update(satellite_id=data.satellite_id, observations=len(data.hour))
            manifest["files"].append(record)
        if not manifest["files"]:
            raise ValueError("No GPS/GLONASS DAT files available")
    except (OSError, ValueError) as exc:
        manifest.update(status="failed", error=str(exc))
    manifest_path = case_root / "manifest.json"
    _write_json(manifest_path, manifest)
    manifest["manifest"] = _file_identity(manifest_path)
    return manifest


def _tec_coverage(path: Path, step: float, tolerance: float, *, legacy: bool) -> dict[str, Any]:
    rows = np.asarray(read_results(path))
    expected = np.arange(math.ceil(24 / step), dtype=float) * step
    expected = expected[expected < 24]
    finite = np.isfinite(rows[:, 1:])
    zero_rows = np.all(rows[:, 1:] == 0, axis=1) if legacy else np.zeros(len(rows), dtype=bool)
    solved = finite.all(axis=1) & ~zero_rows
    grid_complete = len(rows) == len(expected) and bool(np.all(np.abs(rows[:, 0] - expected) <= tolerance))
    return {
        "complete": grid_complete and bool(solved.all()),
        "expected_epochs": len(expected),
        "actual_epochs": len(rows),
        "solved_epochs": int(solved.sum()),
        "time_grid_complete": grid_complete,
        "finite_per_column": {name: int(finite[:, index].sum()) for index, name in enumerate(COEFFICIENTS)},
        "legacy_zero_placeholder_rows": int(zero_rows.sum()),
        "zero_placeholder_policy": "all seven coefficients zero treated as unsolved"
        if legacy
        else "zero values retained",
    }


def read_dcb(path: Path) -> dict[str, dict[str, float]]:
    """Read every satellite/receiver record strictly, without skipping malformed values."""
    result: dict[str, dict[str, float]] = {"satellites": {}, "receivers": {}}
    section = None
    with path.open(encoding="utf-8-sig") as stream:
        for line_number, line in enumerate(stream, 1):
            text = line.strip()
            if not text:
                continue
            if text.startswith("#"):
                label = text.removeprefix("#").strip().lower()
                if label == "dcb sat:":
                    section = "satellites"
                elif label == "dcb rec:":
                    section = "receivers"
                continue
            pattern = r"([GR])\s*(\d{1,3})\s+([^\s]+)" if section == "satellites" else r"([GR])\s+([^\s]+)"
            match = re.fullmatch(pattern, text) if section else None
            if match is None:
                raise ValueError(f"{path}:{line_number}: invalid DCB row or missing section header")
            key = f"{match[1]}{int(match[2]):02d}" if section == "satellites" else match[1]
            value = float(match[len(match.groups())].replace("D", "E").replace("d", "e"))
            if not math.isfinite(value) or key in result[section]:
                raise ValueError(f"{path}:{line_number}: duplicate or nonfinite DCB value")
            result[section][key] = value
    constellations = {key[0] for key in result["satellites"]}
    if not constellations or set(result["receivers"]) != constellations:
        raise ValueError(f"{path}: missing satellite values or receiver constellation coverage")
    return result


def compare_dcb(reference_path: Path, candidate_path: Path, *, atol: float, rtol: float) -> dict[str, Any]:
    reference = read_dcb(reference_path)
    candidate = read_dcb(candidate_path)
    left = {f"{section}:{key}": value for section, values in reference.items() for key, value in values.items()}
    right = {f"{section}:{key}": value for section, values in candidate.items() for key, value in values.items()}
    common = sorted(set(left) & set(right))
    missing = sorted(set(left) - set(right))
    extra = sorted(set(right) - set(left))
    values = {}
    errors = []
    for key in common:
        error = right[key] - left[key]
        finite = math.isfinite(error)
        values[key] = {
            "reference": left[key],
            "candidate": right[key],
            "difference": error if finite else None,
            "passed": finite and abs(error) <= atol + rtol * abs(left[key]),
        }
        if finite:
            errors.append(error)
    complete = not missing and not extra and bool(common) and len(errors) == len(common)
    return {
        "passed": complete and all(value["passed"] for value in values.values()),
        "complete": complete,
        "reference_values": len(left),
        "candidate_values": len(right),
        "matched_values": len(common),
        "missing_candidate_keys": missing,
        "extra_candidate_keys": extra,
        "max_abs_error": max((abs(error) for error in errors), default=None),
        "rmse": math.hypot(*(error / math.sqrt(len(errors)) for error in errors)) if errors else None,
        "bias": math.fsum(error / len(errors) for error in errors) if errors else None,
        "values": values,
    }


def _run_process(
    command: list[str], cwd: Path, timeout: float, *, environment: dict[str, str] | None = None
) -> dict[str, Any]:
    started = time.perf_counter()
    stdout = stderr = b""
    process = None
    status = "failed"
    error = None
    returncode = None
    try:
        process = subprocess.Popen(  # noqa: S603 -- explicit caller-owned executable, argument list, no shell.
            command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            status = "success" if process.returncode == 0 else "failed"
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate(timeout=10)
            status = "timeout"
            error = f"Process exceeded {timeout:g} seconds and was killed"
        returncode = process.returncode
    except OSError as exc:
        error = str(exc)
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=10)
    for name, content in (("stdout.log", stdout), ("stderr.log", stderr)):
        with (cwd / name).open("xb") as stream:
            stream.write(content)
    return {
        "status": status,
        "returncode": returncode,
        "error": error,
        "process_seconds": time.perf_counter() - started,
        "timing_scope": "child process lifetime, including its input/output; excludes executable and input copies",
        "command": command,
        "stdout": _file_identity(cwd / "stdout.log"),
        "stderr": _file_identity(cwd / "stderr.log"),
    }


def _native_run(settings: ValidationSettings, case: dict, case_root: Path, model: str, step: float) -> dict[str, Any]:
    directory = case_root / "native" / model / _step_name(step)
    directory.mkdir(parents=True, exist_ok=False)
    config = _config(settings, model, step)
    result: dict[str, Any] = {"model": model, "step_hours": step, "config": asdict(config), "status": "failed"}
    started = time.perf_counter()
    try:
        data = [read_dat(Path(record["snapshot"])) for record in case["files"]]
        loaded = time.perf_counter()
        solution = reconstruct(data, config)
        solved = time.perf_counter()
        basename = f"{case['site']}_{case['day_of_year']:03d}_{case['year']}"
        tec = directory / f"{basename}.dat"
        dcb = directory / f"DCB_{basename}.dat"
        write_tec_output(tec, solution.rows)
        write_dcb_output(dcb, solution.combined_bias)
        dcb_values = read_dcb(dcb)
        satellites, receivers = split_biases(solution.combined_bias)
        bias_complete = set(dcb_values["satellites"]) == set(satellites) and set(dcb_values["receivers"]) == set(
            receivers
        )
        coverage = _tec_coverage(tec, step, settings.time_tolerance, legacy=False)
        result.update(
            status="success" if coverage["complete"] and bias_complete else "partial",
            coverage=coverage,
            dcb_coverage={
                "complete": bias_complete,
                "satellite_values": len(satellites),
                "receiver_values": len(receivers),
                "total_values": len(satellites) + len(receivers),
            },
            diagnostics=solution.diagnostics,
            tec=_file_identity(tec),
            dcb=_file_identity(dcb),
            parse_seconds=loaded - started,
            solver_seconds=solved - loaded,
        )
    except (ValueError, OSError, ArithmeticError) as exc:
        result["error"] = str(exc)
    result["elapsed_seconds"] = time.perf_counter() - started
    result["timing_scope"] = (
        "in-process parse, reconstruction and output writing; Python/NumPy/SciPy imports already loaded"
    )
    _write_json(directory / "result.json", result)
    return result


def _legacy_run(
    settings: ValidationSettings, case: dict, case_root: Path, step: float, exe_identity: dict | None
) -> dict[str, Any]:
    if settings.legacy_exe is None:
        return {"step_hours": step, "status": "not_run", "reason": "No --legacy-exe supplied"}
    if step < 0.05:
        raise ValueError("Unsafe legacy time step; reference run was not started")
    directory = case_root / "legacy" / _step_name(step)
    directory.mkdir(parents=True, exist_ok=False)
    result: dict[str, Any] = {"step_hours": step, "status": "failed"}
    try:
        executable = directory / "absolTEC.exe"
        copied_exe = _copy_verified(settings.legacy_exe, executable)
        if copied_exe["sha256"] != exe_identity["sha256"]:
            raise ValueError("Legacy executable changed after the immutable run manifest was written")
        input_root = directory / "in"
        input_directory = input_root / str(case["year"]) / f"{case['day_of_year']:03d}" / case["site"]
        input_directory.mkdir(parents=True)
        copies = []
        for record in case["files"]:
            original_snapshot = Path(record["snapshot"])
            copied = _copy_verified(original_snapshot, input_directory / original_snapshot.name)
            if copied["sha256"] != record["sha256"]:
                raise ValueError("Input snapshot changed after its immutable manifest was written")
            copies.append(copied)
        lines = [
            # The original executable has fixed-length Fortran path buffers.
            # Its working directory is this sandbox; relative paths keep input
            # names short even when the validation output directory is deep.
            "in" + os.sep,
            str(settings.elevation_cutoff),
            str(case["year"]),
            str(case["day_of_year"]),
            case["site"],
            str(step),
            str(settings.correction_coefficient),
        ]
        with (directory / "absolTEC.dia").open("x", encoding="ascii", newline="\r\n") as stream:
            stream.write("\n".join(lines) + "\n")
        environment = dict(os.environ)
        environment["FOR_NOERROR_DIALOGS"] = "1"
        result["execution"] = _run_process(
            [str(executable.resolve())], directory, settings.timeout_seconds, environment=environment
        )
        result["dia"] = _file_identity(directory / "absolTEC.dia")
        intact = _file_identity(executable)["sha256"] == copied_exe["sha256"] and all(
            _file_identity(Path(record["snapshot"]))["sha256"] == record["sha256"] for record in copies
        )
        result["inputs_and_executable_unchanged"] = intact
        if result["execution"]["status"] != "success" or not intact:
            raise ValueError("Legacy execution failed/timed out or modified its inputs; output cannot be a reference")
        basename = f"{case['site']}_{case['day_of_year']:03d}_{case['year']}"
        tec = directory / str(case["year"]) / case["site"] / f"{basename}.dat"
        dcb = tec.parent / f"DCB_{basename}.dat"
        coverage = _tec_coverage(tec, step, settings.time_tolerance, legacy=True)
        biases = read_dcb(dcb)
        result.update(
            status="success" if coverage["complete"] else "partial",
            coverage=coverage,
            dcb_coverage={
                "complete": True,
                "satellite_values": len(biases["satellites"]),
                "receiver_values": len(biases["receivers"]),
                "total_values": sum(len(values) for values in biases.values()),
            },
            tec=_file_identity(tec),
            dcb=_file_identity(dcb),
        )
    except (ValueError, OSError, ArithmeticError) as exc:
        result["error"] = str(exc)
    _write_json(directory / "result.json", result)
    return result


def _numerical_tests(output: Path, timeout: float) -> dict[str, Any]:
    directory = output / "numerical-tests"
    directory.mkdir()
    names = (
        "test_absoltec_solver.py",
        "test_absoltec_science.py",
        "test_absoltec_geometry.py",
        "test_absoltec_bias.py",
        "test_absoltec_legacy.py",
        "test_absoltec_io.py",
    )
    paths = [_ROOT / "tests" / name for name in names]
    xml_path = directory / "junit.xml"
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(_ROOT) + (
        os.pathsep + environment["PYTHONPATH"] if environment.get("PYTHONPATH") else ""
    )
    result = _run_process(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            *(str(path) for path in paths),
            f"--junitxml={xml_path}",
        ],
        directory,
        timeout,
        environment=environment,
    )
    try:
        root = ET.parse(xml_path).getroot()  # noqa: S314 -- JUnit was just generated by the selected local test process.
        suites = list(root.iter("testsuite"))
        counts = {
            name: sum(int(suite.attrib.get(name, "0")) for suite in suites)
            for name in ("tests", "failures", "errors", "skipped")
        }
        result["counts"] = counts
        result["junit"] = _file_identity(xml_path)
        if (
            result["status"] != "success"
            or counts["tests"] == 0
            or any(counts[name] for name in ("failures", "errors", "skipped"))
        ):
            result["status"] = "failed"
    except (OSError, ValueError, ET.ParseError) as exc:
        result.update(status="failed", error=f"Missing/invalid numerical test evidence: {exc}")
    return result


def _comparisons(settings: ValidationSettings, native: list[dict], legacy: list[dict]) -> list[dict[str, Any]]:
    results = []
    for candidate in native:
        comparison: dict[str, Any] = {
            "model": candidate["model"],
            "step_hours": candidate["step_hours"],
            "status": "not_compared",
        }
        reference = next((run for run in legacy if run["step_hours"] == candidate["step_hours"]), None)
        if reference is None or reference["status"] == "not_run":
            comparison["reason"] = "No same-grid legacy reference; no interpolation or resampling performed"
        elif reference["status"] != "success" or candidate["status"] != "success":
            comparison.update(status="failed", reason="Native or reference output failed its full coverage gate")
        else:
            try:
                comparison["tec"] = compare_files(
                    Path(reference["tec"]["path"]),
                    Path(candidate["tec"]["path"]),
                    atol=settings.atol,
                    rtol=settings.rtol,
                    time_tolerance=settings.time_tolerance,
                    zero_rows_missing=True,
                )
                comparison["dcb"] = compare_dcb(
                    Path(reference["dcb"]["path"]),
                    Path(candidate["dcb"]["path"]),
                    atol=settings.atol,
                    rtol=settings.rtol,
                )
                comparison["status"] = (
                    "passed" if comparison["tec"]["passed"] and comparison["dcb"]["passed"] else "failed"
                )
            except (OSError, ValueError) as exc:
                comparison.update(status="failed", error=str(exc))
        results.append(comparison)
    return results


def _aggregate(cases: list[dict], numerical: dict, models: tuple[str, ...], has_exe: bool) -> dict[str, Any]:
    native = [run for case in cases for run in case["native"]]
    legacy = [run for case in cases for run in case["legacy"]]
    cases_ready = bool(cases) and all(case["input_status"] == "ready" for case in cases)
    native_pass = cases_ready and bool(native) and all(run["status"] == "success" for run in native)
    reference_pass = cases_ready and bool(legacy) and all(run["status"] == "success" for run in legacy)
    parity = {}
    for model in models:
        comparisons = [
            result
            for case in cases
            for result in case["comparisons"]
            if result["model"] == model and result["status"] != "not_compared"
        ]
        parity[model] = (
            "not_run"
            if not has_exe or not comparisons
            else "passed"
            if cases_ready and all(result["status"] == "passed" for result in comparisons)
            else "failed"
        )
    gates = {
        "numerical_tests": "passed" if numerical["status"] == "success" else numerical["status"],
        "native_coverage": "passed" if native_pass else "failed",
        "reference_coverage": "not_run" if not has_exe else "passed" if reference_pass else "failed",
        "same_grid_parity_by_model": parity,
        "external_scientific_accuracy": "unverified",
    }
    gates["validation_passed"] = (
        gates["numerical_tests"] == "passed"
        and native_pass
        and reference_pass
        and parity.get("legacy-2017") == "passed"
    )
    gates["physical_accuracy_claim"] = (
        "None: numerical consistency and legacy agreement do not validate ionospheric truth"
    )
    return gates


def run_validation(settings: ValidationSettings) -> dict[str, Any]:
    """Execute reproducible cases; existing output is always an error, never reused."""
    _validate_settings(settings)
    output = settings.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    parameters = asdict(settings)
    parameters.update(
        dat_path=str(settings.dat_path.resolve()),
        output_dir=str(output),
        legacy_exe=str(settings.legacy_exe.resolve()) if settings.legacy_exe else None,
        input_provenance=str(settings.input_provenance.resolve()) if settings.input_provenance else None,
    )
    executable = _file_identity(settings.legacy_exe) if settings.legacy_exe else None
    versions = {
        "python": sys.version,
        "native_package": __version__,
        "numpy": np.__version__,
        "scipy": importlib.metadata.version("scipy"),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "logical_cpus": os.cpu_count(),
        "thread_environment": {
            name: os.environ.get(name) for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS")
        },
    }
    code_files = [
        *_ROOT.joinpath("absoltec_native").glob("*.py"),
        Path(__file__),
        _ROOT / "scripts" / "compare_absoltec.py",
    ]
    snapshots = []
    case_roots = []
    for day in settings.days:
        for site in settings.sites:
            case_root = output / "cases" / f"{settings.year}_{day:03d}_{site}"
            case_root.parent.mkdir(exist_ok=True)
            snapshots.append(_snapshot_case(settings, case_root, day, site))
            case_roots.append(case_root)
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "parameters": parameters,
        "versions": versions,
        "legacy_executable": executable,
        "input_provenance": _copy_verified(settings.input_provenance, output / "input-provenance.json")
        if settings.input_provenance
        else None,
        "implementation_files": [_file_identity(path) for path in sorted(code_files)],
        "cases": [case["manifest"] for case in snapshots],
    }
    _write_json(output / "manifest.json", manifest)
    numerical = (
        _numerical_tests(output, settings.timeout_seconds)
        if settings.run_numerical_tests
        else {
            "status": "not_run",
            "reason": "Use --run-numerical-tests to execute the independent local numerical suite",
        }
    )
    cases = []
    for snapshot, case_root in zip(snapshots, case_roots, strict=True):
        print(
            f"Validating year={snapshot['year']} day={snapshot['day_of_year']:03d} site={snapshot['site']}", flush=True
        )
        case = {
            "year": snapshot["year"],
            "day_of_year": snapshot["day_of_year"],
            "site": snapshot["site"],
            "manifest": snapshot["manifest"],
            "input_status": snapshot["status"],
            "native": [],
            "legacy": [],
            "comparisons": [],
        }
        if snapshot["status"] == "ready":
            case["native"] = [
                _native_run(settings, snapshot, case_root, model, step)
                for model in settings.models
                for step in settings.steps
            ]
            case["legacy"] = [
                _legacy_run(settings, snapshot, case_root, step, executable) for step in settings.legacy_steps
            ]
            case["comparisons"] = _comparisons(settings, case["native"], case["legacy"])
            case["input_snapshots_unchanged"] = all(
                _file_identity(Path(record["snapshot"]))["sha256"] == record["sha256"] for record in snapshot["files"]
            )
            if not case["input_snapshots_unchanged"]:
                case["input_status"] = "modified"
        else:
            case["error"] = snapshot["error"]
        cases.append(case)
    report = {
        "schema_version": 1,
        "manifest": _file_identity(output / "manifest.json"),
        "versions": versions,
        "parameters": parameters,
        "numerical_tests": numerical,
        "cases": cases,
        "acceptance": _aggregate(cases, numerical, settings.models, settings.legacy_exe is not None),
        "limitations": [
            "DAT provenance does not certify earlier RINEX header adaptations or validate the input conversion.",
            "Adjacent-day context is not loaded in either implementation.",
            "Native fine grids without a same-grid safe legacy reference are not compared.",
            "A completed process and the word Done do not establish output coverage.",
            "Legacy all-zero coefficient rows are treated as unsolved placeholders.",
            "This runner measures consistency, coverage and agreement; external physical accuracy remains unverified.",
        ],
    }
    _write_json(output / "report.json", report)
    return report


def _parse_steps(text: str) -> tuple[float, ...]:
    try:
        return tuple(float(Fraction(token.strip())) for token in text.split(","))
    except (ValueError, ZeroDivisionError) as exc:
        raise argparse.ArgumentTypeError(
            "Steps must be comma-separated decimals or fractions, e.g. 0.5,0.05,1/60"
        ) from exc


def _parse_days(text: str, year: int) -> tuple[int, ...]:
    result = set()
    for part in text.split(","):
        match = re.fullmatch(r"\s*(\d{1,3})(?:-(\d{1,3}))?\s*", part)
        if not match:
            raise ValueError(f"Invalid day selection: {part!r}")
        start, end = int(match[1]), int(match[2] or match[1])
        if start > end:
            raise ValueError(f"Reversed day range: {part}")
        result.update(range(start, end + 1))
    if not result or min(result) < 1 or max(result) > (366 if calendar.isleap(year) else 365):
        raise ValueError("Days are outside the requested year")
    return tuple(sorted(result))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dat-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True, help="new isolated directory; must not already exist")
    parser.add_argument("--legacy-exe", type=Path)
    parser.add_argument("--input-provenance", type=Path, help="copy and hash the corpus preparation/provenance record")
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--days", required=True, help="comma-separated days or inclusive ranges")
    parser.add_argument("--sites", required=True, help="comma-separated four-character station directory names")
    parser.add_argument(
        "--steps", type=_parse_steps, default=(0.5, 0.05, 1 / 60), help="native grids; default 0.5,0.05,1/60 hours"
    )
    parser.add_argument("--models", default="legacy-2017,published")
    parser.add_argument(
        "--legacy-steps", type=_parse_steps, default=(0.5,), help="reference grids, default 0.5 hours; never below 0.05"
    )
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--atol", type=float, default=0.001)
    parser.add_argument("--rtol", type=float, default=0.0)
    parser.add_argument("--time-tolerance", type=float, default=1e-6)
    parser.add_argument("--elevation-cutoff", type=float, default=10.0)
    parser.add_argument("--correction-coefficient", type=float, default=0.97)
    parser.add_argument("--window-hours", type=float, default=1.0)
    parser.add_argument("--run-numerical-tests", action="store_true")
    args = parser.parse_args(argv)
    try:
        settings = ValidationSettings(
            dat_path=args.dat_path,
            output_dir=args.output_dir,
            year=args.year,
            days=_parse_days(args.days, args.year),
            sites=tuple(site.strip() for site in args.sites.split(",")),
            steps=args.steps,
            models=tuple(model.strip() for model in args.models.split(",")),
            legacy_exe=args.legacy_exe,
            legacy_steps=args.legacy_steps,
            timeout_seconds=args.timeout_seconds,
            atol=args.atol,
            rtol=args.rtol,
            time_tolerance=args.time_tolerance,
            elevation_cutoff=args.elevation_cutoff,
            correction_coefficient=args.correction_coefficient,
            window_hours=args.window_hours,
            run_numerical_tests=args.run_numerical_tests,
            input_provenance=args.input_provenance,
        )
        report = run_validation(settings)
    except (OSError, ValueError) as exc:
        print(f"Validation could not start: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report["acceptance"], indent=2, allow_nan=False))
    print(f"Report: {settings.output_dir.resolve() / 'report.json'}")
    return 0 if report["acceptance"]["validation_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
