"""Scientific parity checks must expose missing data and mismatched time grids."""

import json
import math

import pytest

from scripts.compare_absoltec import COEFFICIENTS, compare_arrays, compare_files, main, read_results


def _row(time, value=10.0):
    return (time, *(value for _ in COEFFICIENTS))


def test_complete_finite_results_compare_all_seven_columns():
    report = compare_arrays([_row(0), _row(0.5, 20)], [_row(0), _row(0.5, 20)])
    assert report["passed"]
    assert report["complete"]
    assert tuple(report["columns"]) == COEFFICIENTS
    for result in report["columns"].values():
        assert result["matched_finite_count"] == 2
        assert result["rmse"] == 0


def test_time_grids_are_aligned_by_time_and_never_by_row_or_interpolated():
    reference = [_row(0, 10), _row(0.5, 20), _row(1, 30)]
    candidate = [_row(0, 10), _row(1, 30)]
    report = compare_arrays(reference, candidate)
    assert not report["passed"]
    assert not report["complete"]
    assert report["alignment"]["unmatched_reference_times"] == [0.5]
    assert report["columns"]["I_v"]["rmse"] == 0
    assert report["columns"]["I_v"]["missing_candidate"] == 1
    assert compare_arrays(reference, candidate, allow_incomplete=True)["passed"]


def test_tolerant_time_match_does_not_relax_coefficient_tolerance():
    report = compare_arrays([_row(0.5)], [_row(0.5000002, 11)], time_tolerance=1e-6, atol=0.5)
    assert not report["passed"]
    assert report["alignment"]["paired_epochs"] == 1
    assert report["columns"]["I_v"]["failing_tolerance_count"] == 1


def test_ambiguous_time_matching_is_invalid_instead_of_choosing_a_row():
    with pytest.raises(ValueError, match="Ambiguous"):
        compare_arrays([_row(0.5)], [_row(0.4), _row(0.6)], time_tolerance=0.2)
    with pytest.raises(ValueError, match="multiple reference"):
        compare_arrays([_row(0.4), _row(0.6)], [_row(0.5)], time_tolerance=0.2)


def test_nan_and_infinity_are_missing_even_when_identical_on_both_sides():
    reference = [_row(0, math.nan), _row(0.5, math.inf)]
    report = compare_arrays(reference, reference, allow_incomplete=True)
    assert not report["passed"]
    assert not report["complete"]
    result = report["columns"]["I_v"]
    assert result["matched_finite_count"] == 0
    assert result["missing_reference"] == result["missing_candidate"] == result["missing_both"] == 2
    assert result["max_abs_error"] is None
    json.dumps(report, allow_nan=False)


def test_nonfinite_values_fail_coverage_but_can_be_explicitly_allowed():
    reference = [_row(0), _row(0.5)]
    candidate = [_row(0), (0.5, math.nan, 10, 10, 10, 10, 10, 10)]
    report = compare_arrays(reference, candidate)
    assert not report["passed"]
    assert report["columns"]["I_v"]["missing_candidate"] == 1
    assert report["columns"]["G_lon"]["missing_candidate"] == 0
    assert compare_arrays(reference, candidate, allow_incomplete=True)["passed"]


def test_no_overlapping_epochs_never_passes():
    report = compare_arrays([_row(0)], [_row(1)], allow_incomplete=True)
    assert not report["passed"]
    assert report["alignment"]["paired_epochs"] == 0
    assert report["columns"]["I_v"]["rmse"] is None


def test_zero_coefficients_are_preserved_until_explicitly_marked_missing():
    rows = [_row(0, 0)]
    assert compare_arrays(rows, rows)["passed"]
    report = compare_arrays(rows, rows, zero_rows_missing=True, allow_incomplete=True)
    assert not report["passed"]
    assert report["zero_rows_marked_missing"] == {"reference": 1, "candidate": 1}


def test_thresholds_are_relative_to_reference_and_bias_has_candidate_sign():
    reference = [_row(0, 10), _row(0.5, 20)]
    candidate = [_row(0, 11), _row(0.5, 19)]
    report = compare_arrays(reference, candidate, rtol=0.1)
    assert report["passed"]
    assert report["columns"]["I_v"]["rmse"] == pytest.approx(1)
    assert report["columns"]["I_v"]["bias"] == 0
    assert not compare_arrays([_row(0, 10)], [_row(0, 11.1)], rtol=0.1)["passed"]
    assert compare_arrays([_row(0, 0)], [_row(0, -0.1)], atol=0.1)["columns"]["I_v"]["bias"] == -0.1


@pytest.mark.parametrize("rows", [[], [(0, 1)], [_row(math.nan)], [_row(0.5), _row(0)], [_row(0), _row(0)]])
def test_empty_malformed_or_invalid_time_rows_are_invalid(rows):
    with pytest.raises(ValueError, match="reference"):
        compare_arrays(rows, [_row(0)])


@pytest.mark.parametrize(("option", "value"), [("atol", -1), ("rtol", math.nan), ("time_tolerance", math.inf)])
def test_invalid_tolerances_are_rejected(option, value):
    with pytest.raises(ValueError, match=option):
        compare_arrays([_row(0)], [_row(0)], **{option: value})


def test_difference_overflow_is_an_explicit_failure_and_json_safe():
    report = compare_arrays([_row(0, -1e308)], [_row(0, 1e308)])
    assert not report["passed"]
    assert report["columns"]["I_v"]["metric_overflow"]
    assert report["columns"]["I_v"]["rmse"] is None
    json.dumps(report, allow_nan=False)


def test_dat_comments_whitespace_and_fortran_exponents(tmp_path):
    reference = tmp_path / "reference.dat"
    reference.write_text("# UT I_v G_lon G_lat G_q_lon G_q_lat G_t G_q_t\n\n0 1D+1 1d1 10 10 10 10 10\n")
    candidate = tmp_path / "candidate.dat"
    candidate.write_text("  # Arbitrary comment\n0 10 10 10 10 10 10 10\n")
    report = compare_files(reference, candidate)
    assert report["passed"]
    assert report["files"]["reference"] == str(reference)
    assert read_results(reference) == [_row(0)]


@pytest.mark.parametrize("content", ["# Empty\n", "0 1 2\n", "0 1 2 3 4 5 6 ******\n"])
def test_bad_dat_fails_instead_of_dropping_rows(tmp_path, content):
    path = tmp_path / "bad.dat"
    path.write_text(content)
    with pytest.raises(ValueError, match=r"bad\.dat"):
        read_results(path)


def test_cli_exit_codes_distinguish_parity_failure_and_invalid_files(tmp_path, capsys):
    reference = tmp_path / "reference.dat"
    candidate = tmp_path / "candidate.dat"
    reference.write_text("0 10 10 10 10 10 10 10\n")
    candidate.write_text("0 10 10 10 10 10 10 10\n")
    args = [str(reference), str(candidate), "--json"]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["passed"]
    candidate.write_text("0 11 10 10 10 10 10 10\n")
    assert main(args) == 1
    assert not json.loads(capsys.readouterr().out)["passed"]
    candidate.write_text("broken\n")
    assert main(args) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "error"
