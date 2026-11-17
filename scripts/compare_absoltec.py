"""Compare complete AbsolTEC results without interpolation or reference calibration.

Each result has eight whitespace-separated fields: UT followed by seven model
coefficients. NaN and infinity in a coefficient remain explicit missing values.
UT must be finite, unique, and increasing. Defaults require the same time grid
and finite values in every column; ``--allow-incomplete`` relaxes coverage only.
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

COEFFICIENTS = ("I_v", "G_lon", "G_lat", "G_q_lon", "G_q_lat", "G_t", "G_q_t")
_FIELD_COUNT = len(COEFFICIENTS) + 1


def _validated_rows(rows: Iterable[Sequence[float]], label: str) -> list[tuple[float, ...]]:
    result: list[tuple[float, ...]] = []
    for row_number, row in enumerate(rows, start=1):
        if len(row) != _FIELD_COUNT:
            raise ValueError(f"{label} row {row_number}: expected {_FIELD_COUNT} fields, got {len(row)}")
        try:
            values = tuple(float(value) for value in row)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} row {row_number}: nonnumeric field") from exc
        if not math.isfinite(values[0]):
            raise ValueError(f"{label} row {row_number}: UT must be finite")
        if result and values[0] <= result[-1][0]:
            raise ValueError(f"{label} row {row_number}: UT must be unique and strictly increasing")
        result.append(values)
    if not result:
        raise ValueError(f"{label}: no result rows")
    return result


def read_results(path: str | Path) -> list[tuple[float, ...]]:
    """Read a DAT result strictly, accepting comments and Fortran D exponents.

    Blank/comment lines are file formatting, not observations. An empty file or
    any malformed observation raises ValueError, with the source line number.
    Asterisks (Fortran overflow fields) are malformed rather than silent NaNs.
    """
    source = Path(path)
    rows: list[tuple[float, ...]] = []
    with source.open(encoding="utf-8-sig") as stream:
        for line_number, line in enumerate(stream, start=1):
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            fields = text.split()
            if len(fields) != _FIELD_COUNT:
                raise ValueError(f"{source}:{line_number}: expected {_FIELD_COUNT} fields, got {len(fields)}")
            try:
                rows.append(tuple(float(field.replace("D", "E").replace("d", "e")) for field in fields))
            except ValueError as exc:
                raise ValueError(f"{source}:{line_number}: nonnumeric field") from exc
    return _validated_rows(rows, str(source))


def _time_pairs(
    reference: Sequence[tuple[float, ...]], candidate: Sequence[tuple[float, ...]], tolerance: float
) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    reference_times = [row[0] for row in reference]
    candidate_times = [row[0] for row in candidate]
    pairs = []
    matched_candidates: set[int] = set()
    for reference_index, time in enumerate(reference_times):
        first = bisect.bisect_left(candidate_times, time - tolerance)
        last = bisect.bisect_right(candidate_times, time + tolerance)
        if last - first > 1:
            raise ValueError(f"Ambiguous time match at reference UT={time}; reduce time_tolerance")
        if first == last:
            continue
        if first in matched_candidates:
            raise ValueError(f"Candidate UT={candidate_times[first]} matches multiple reference times")
        matched_candidates.add(first)
        pairs.append((reference_index, first))
    matched_references = {reference_index for reference_index, _ in pairs}
    return (
        pairs,
        [index for index in range(len(reference)) if index not in matched_references],
        [index for index in range(len(candidate)) if index not in matched_candidates],
    )


def _coefficient_report(
    reference: Sequence[tuple[float, ...]],
    candidate: Sequence[tuple[float, ...]],
    pairs: Sequence[tuple[int, int]],
    unmatched_reference: Sequence[int],
    unmatched_candidate: Sequence[int],
    column: int,
    atol: float,
    rtol: float,
) -> dict[str, Any]:
    errors: list[float] = []
    failing = 0
    overflow = False
    missing_reference = len(unmatched_candidate)
    missing_candidate = len(unmatched_reference)
    missing_both = 0
    for reference_index, candidate_index in pairs:
        reference_value = reference[reference_index][column]
        candidate_value = candidate[candidate_index][column]
        reference_finite = math.isfinite(reference_value)
        candidate_finite = math.isfinite(candidate_value)
        missing_reference += not reference_finite
        missing_candidate += not candidate_finite
        missing_both += not reference_finite and not candidate_finite
        if not (reference_finite and candidate_finite):
            continue
        error = candidate_value - reference_value
        errors.append(error)
        if not math.isfinite(error):
            overflow = True
            failing += 1
        elif abs(error) > atol + rtol * abs(reference_value):
            failing += 1
    for index in unmatched_reference:
        if not math.isfinite(reference[index][column]):
            missing_reference += 1
            missing_both += 1
    for index in unmatched_candidate:
        if not math.isfinite(candidate[index][column]):
            missing_candidate += 1
            missing_both += 1

    if errors and not overflow:
        normalizer = math.sqrt(len(errors))
        max_abs_error = max(abs(error) for error in errors)
        rmse = math.hypot(*(error / normalizer for error in errors))
        bias = math.fsum(error / len(errors) for error in errors)
    else:
        max_abs_error = rmse = bias = None
    return {
        "matched_finite_count": len(errors),
        "missing_reference": missing_reference,
        "missing_candidate": missing_candidate,
        "missing_both": missing_both,
        "nonfinite_reference": sum(not math.isfinite(row[column]) for row in reference),
        "nonfinite_candidate": sum(not math.isfinite(row[column]) for row in candidate),
        "failing_tolerance_count": failing,
        "max_abs_error": max_abs_error,
        "rmse": rmse,
        "bias": bias,
        "metric_overflow": overflow,
        "passes_tolerance": bool(errors) and failing == 0,
    }


def compare_arrays(
    reference_rows: Iterable[Sequence[float]],
    candidate_rows: Iterable[Sequence[float]],
    *,
    atol: float = 1e-6,
    rtol: float = 0.0,
    time_tolerance: float = 1e-6,
    allow_incomplete: bool = False,
    zero_rows_missing: bool = False,
) -> dict[str, Any]:
    """Return a JSON-safe parity report, with bias = candidate - reference.

    A finite pair passes when ``abs(candidate-reference) <= atol +
    rtol*abs(reference)``. Missing counts cover the union of both time grids;
    nonfinite counts describe only rows physically present in that input.
    Missing values never count as matching, even when both inputs contain NaN.
    Every coefficient must have at least one finite pair to pass, including
    with allow_incomplete. Zero values are valid unless all seven coefficients
    are zero and zero_rows_missing is explicitly selected.
    """
    for name, value in (("atol", atol), ("rtol", rtol), ("time_tolerance", time_tolerance)):
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{name} must be finite and nonnegative")
    reference = _validated_rows(reference_rows, "reference")
    candidate = _validated_rows(candidate_rows, "candidate")
    zero_counts = {"reference": 0, "candidate": 0}
    if zero_rows_missing:
        for label, rows in (("reference", reference), ("candidate", candidate)):
            for index, row in enumerate(rows):
                if all(value == 0.0 for value in row[1:]):
                    rows[index] = (row[0], *(math.nan for _ in COEFFICIENTS))
                    zero_counts[label] += 1
    pairs, unmatched_reference, unmatched_candidate = _time_pairs(reference, candidate, time_tolerance)
    columns = {
        name: _coefficient_report(
            reference, candidate, pairs, unmatched_reference, unmatched_candidate, index, atol, rtol
        )
        for index, name in enumerate(COEFFICIENTS, start=1)
    }
    complete = not unmatched_reference and not unmatched_candidate
    complete = complete and all(
        result["missing_reference"] == 0 and result["missing_candidate"] == 0 for result in columns.values()
    )
    passed = (complete or allow_incomplete) and all(result["passes_tolerance"] for result in columns.values())
    issues = []
    if unmatched_reference or unmatched_candidate:
        issues.append("Time grids differ; observations were not interpolated.")
    if any(result["nonfinite_reference"] or result["nonfinite_candidate"] for result in columns.values()):
        issues.append("Nonfinite coefficients are missing values, including when both inputs are nonfinite.")
    if any(not result["matched_finite_count"] for result in columns.values()):
        issues.append("At least one coefficient has no finite comparison pairs.")
    if any(result["failing_tolerance_count"] for result in columns.values()):
        issues.append("At least one finite coefficient pair exceeds the error tolerance.")
    if any(result["metric_overflow"] for result in columns.values()):
        issues.append("Difference arithmetic overflowed; affected metrics are null and the comparison fails.")
    return {
        "schema_version": 1,
        "status": "passed" if passed else "failed",
        "passed": passed,
        "complete": complete,
        "thresholds": {
            "atol": atol,
            "rtol": rtol,
            "time_tolerance_hours": time_tolerance,
            "relative_to": "reference",
        },
        "allow_incomplete": allow_incomplete,
        "zero_rows_missing": zero_rows_missing,
        "zero_rows_marked_missing": zero_counts,
        "alignment": {
            "reference_rows": len(reference),
            "candidate_rows": len(candidate),
            "paired_epochs": len(pairs),
            "unmatched_reference_times": [reference[index][0] for index in unmatched_reference],
            "unmatched_candidate_times": [candidate[index][0] for index in unmatched_candidate],
            "max_time_difference_hours": max((abs(candidate[j][0] - reference[i][0]) for i, j in pairs), default=None),
        },
        "columns": columns,
        "issues": issues,
    }


def compare_files(reference_path: str | Path, candidate_path: str | Path, **options: Any) -> dict[str, Any]:
    """Read and compare two DAT results; malformed inputs raise ValueError."""
    report = compare_arrays(read_results(reference_path), read_results(candidate_path), **options)
    report["files"] = {"reference": str(reference_path), "candidate": str(candidate_path)}
    return report


def _nonnegative_finite(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a finite nonnegative number") from exc
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError("must be a finite nonnegative number")
    return parsed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path, help="original AbsolTEC eight-column DAT result")
    parser.add_argument("candidate", type=Path, help="replacement eight-column DAT result")
    parser.add_argument("--atol", type=_nonnegative_finite, default=1e-6, help="absolute coefficient tolerance")
    parser.add_argument("--rtol", type=_nonnegative_finite, default=0.0, help="relative tolerance against reference")
    parser.add_argument("--time-tolerance", type=_nonnegative_finite, default=1e-6, help="UT matching tolerance, hours")
    parser.add_argument(
        "--allow-incomplete", action="store_true", help="allow unmatched/missing values; report coverage"
    )
    parser.add_argument(
        "--zero-rows-missing", action="store_true", help="treat rows whose seven coefficients are all zero as missing"
    )
    parser.add_argument("--json", action="store_true", help="write a machine-readable report to stdout")
    args = parser.parse_args(argv)
    try:
        report = compare_files(
            args.reference,
            args.candidate,
            atol=args.atol,
            rtol=args.rtol,
            time_tolerance=args.time_tolerance,
            allow_incomplete=args.allow_incomplete,
            zero_rows_missing=args.zero_rows_missing,
        )
    except (OSError, UnicodeError, TypeError, ValueError) as exc:
        if args.json:
            print(json.dumps({"status": "error", "passed": False, "error": str(exc)}, allow_nan=False))
        else:
            print(f"Invalid input: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2, allow_nan=False))
    else:
        print(f"Parity {report['status']}; complete coverage: {report['complete']}")
        for name, result in report["columns"].items():
            print(
                f"{name}: finite pairs={result['matched_finite_count']}, "
                f"missing reference/candidate={result['missing_reference']}/{result['missing_candidate']}, "
                f"max error={result['max_abs_error']}, RMSE={result['rmse']}, bias={result['bias']}"
            )
        for issue in report["issues"]:
            print(issue)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
