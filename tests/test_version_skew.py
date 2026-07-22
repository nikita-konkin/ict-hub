"""Tests for cross-repo version-skew detection.

The UI and the converter images are built by separate pipelines and rolled out
independently, so a registry that gained a flag can reach production before the
image that understands it. argparse then exits immediately with a terse
"unrecognized arguments" that gives no hint of the real cause.
"""

import pytest

from app.registry import detect_runner_version_skew


def test_detects_argparse_rejection_and_names_the_flags():
    log = "run_absoltec.py: error: unrecognized arguments: --jobs 8 --skip-existing"

    message = detect_runner_version_skew(log, "abstec-suite")

    assert message is not None
    assert "--jobs" in message
    assert "--skip-existing" in message
    # The operator needs to know it is a deployment problem, not bad input.
    assert "newer than the image" in message


def test_names_the_image_to_pull():
    log = "error: unrecognized arguments: --jobs 8"

    message = detect_runner_version_skew(log, "abstec-suite")

    assert "abstec-suite" in message


def test_flag_values_are_not_reported_as_flags():
    log = "error: unrecognized arguments: --jobs 8 --min-data-rows 0"

    message = detect_runner_version_skew(log, "abstec-suite")

    assert "--jobs --min-data-rows" in message
    assert " 8 " not in message


@pytest.mark.parametrize(
    "log",
    [
        "",
        None,
        "Processing year=2025 day=008 site=aksu0080 (1/3)",
        "Completed 1653 / 11034",
        "RuntimeError: absolTEC exited with code 64 in the XP guest",
    ],
)
def test_ordinary_output_is_not_flagged(log):
    assert detect_runner_version_skew(log, "abstec-suite") is None


def test_works_without_a_converter_name():
    message = detect_runner_version_skew("error: unrecognized arguments: --jobs 8")

    assert message is not None
    assert "converter" in message


def test_case_insensitive():
    assert detect_runner_version_skew("Unrecognized Arguments: --jobs", "abstec-suite")
