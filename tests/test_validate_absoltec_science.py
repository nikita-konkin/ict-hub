"""Reference validation must fail closed on absent, empty or incomplete evidence."""

import json
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from absoltec_native.bias import write_dcb_output
from absoltec_native.io import write_tec_output
from scripts import validate_absoltec_science as validation


def _dat(root, satellite="G01", *, day=1):
    directory = root / "2026" / f"{day:03d}" / "mork"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"mork_{satellite}_{day:03d}_26.dat"
    path.write_text(
        f"# Satellite: {satellite}\n# Site: mork\n# Position (L, B, H): 48, 56, 100\n"
        "# Columns: tsn, hour, el, az, tec.l1l2, tec.c1p2, validity\n"
        "1 0 40 90 15 20 0\n2 0.5 50 95 16 21 0\n3 1 60 100 17 22 0\n",
        encoding="ascii",
    )
    return path


def _solution(step=0.5, *, nan=False):
    times = np.arange(0, 24, step)
    coefficients = np.tile([10, 1, 2, 3, 4, 5, 6], (len(times), 1)).astype(float)
    if nan:
        coefficients[:] = np.nan
    return SimpleNamespace(
        rows=np.column_stack((times, coefficients)),
        combined_bias={"G01": -20.0, "R01": -30.0},
        diagnostics={"test_fixture": True},
    )


def _settings(tmp_path, **changes):
    source = tmp_path / "source"
    _dat(source)
    _dat(source, "R01")
    exe = tmp_path / "absolTEC.exe"
    exe.write_bytes(b"test executable; never launched")
    settings = validation.ValidationSettings(
        dat_path=source,
        output_dir=tmp_path / "new-output",
        year=2026,
        days=(1,),
        sites=("mork",),
        steps=(0.5,),
        models=("legacy-2017",),
        legacy_exe=exe,
        run_numerical_tests=True,
    )
    return replace(settings, **changes)


class FakeProcess:
    def __init__(self, command, *, cwd, produce=True, zero=False, shorten=False, returncode=0, **options):
        self.command = command
        self.options = options
        self.returncode = returncode
        self.killed = False
        self.communications = 0
        self.cwd = Path(cwd)
        if produce:
            lines = (self.cwd / "absolTEC.dia").read_text().splitlines()
            year, day, site, step = int(lines[2]), int(lines[3]), lines[4], float(lines[5])
            directory = self.cwd / str(year) / site
            directory.mkdir(parents=True)
            result = _solution(step)
            if zero:
                result.rows[:, 1:] = 0
            if shorten:
                result.rows = result.rows[:-1]
            write_tec_output(directory / f"{site}_{day:03d}_{year}.dat", result.rows)
            write_dcb_output(directory / f"DCB_{site}_{day:03d}_{year}.dat", result.combined_bias)

    def communicate(self, timeout):
        self.communications += 1
        return b"Done\r\n", b""

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout):
        return self.returncode


@pytest.fixture
def mocked_engines(monkeypatch):
    processes = []

    def launch(command, **options):
        process = FakeProcess(command, **options)
        processes.append(process)
        return process

    monkeypatch.setattr(validation, "_popen", launch)
    monkeypatch.setattr(validation, "reconstruct", lambda data, config: _solution(config.time_step_hours))
    monkeypatch.setattr(
        validation,
        "_numerical_tests",
        lambda output, timeout: {
            "status": "success",
            "counts": {"tests": 25, "failures": 0, "errors": 0, "skipped": 0},
        },
    )
    return processes


def test_complete_fresh_outputs_have_separate_gates_and_immutable_identity(tmp_path, mocked_engines):
    settings = _settings(tmp_path)
    excluded = _dat(settings.dat_path, "E01")
    report = validation.run_validation(settings)
    assert report["acceptance"]["validation_passed"]
    assert report["acceptance"]["numerical_tests"] == "passed"
    assert report["acceptance"]["same_grid_parity_by_model"]["legacy-2017"] == "passed"
    assert report["acceptance"]["external_scientific_accuracy"] == "unverified"
    assert (settings.output_dir / "report.json").is_file()
    manifest = json.loads((settings.output_dir / "manifest.json").read_text())
    assert manifest["legacy_executable"]["sha256"] == validation._file_identity(settings.legacy_exe)["sha256"]
    assert manifest["implementation_files"]
    case = report["cases"][0]
    case_manifest = json.loads(Path(case["manifest"]["path"]).read_text())
    assert len(case_manifest["files"]) == 2
    assert len(case_manifest["excluded_files"]) == 1
    assert case_manifest["excluded_files"][0]["sha256"] == validation._file_identity(excluded)["sha256"]
    process = mocked_engines[0]
    assert len(list((process.cwd / "in" / "2026" / "001" / "mork").glob("*.dat"))) == 2
    assert process.options["stdin"] == subprocess.DEVNULL
    assert process.command == [str(process.cwd / "absolTEC.exe")]
    assert case["legacy"][0]["inputs_and_executable_unchanged"]
    assert case["comparisons"][0]["dcb"]["matched_values"] == 4


def test_existing_directory_and_old_reference_are_never_reused(tmp_path, mocked_engines):
    settings = _settings(tmp_path)
    settings.output_dir.mkdir()
    old = settings.output_dir / "old-reference.dat"
    old.write_text("old data")
    with pytest.raises(ValueError, match="new directory"):
        validation.run_validation(settings)
    assert old.read_text() == "old data"
    assert not mocked_engines


def test_legacy_uses_short_relative_input_paths_in_a_deep_sandbox(tmp_path, mocked_engines):
    settings = _settings(tmp_path, output_dir=tmp_path / ("long-validation-directory-" * 3) / "fresh")
    report = validation.run_validation(settings)
    process = mocked_engines[0]
    input_root = Path((process.cwd / "absolTEC.dia").read_text().splitlines()[0])
    assert not input_root.is_absolute()
    inputs = list((process.cwd / input_root / "2026" / "001" / "mork").glob("*.dat"))
    assert len(inputs) == 2
    assert all(len(str(path.relative_to(process.cwd))) < 120 for path in inputs)
    assert all(len(str(path.resolve())) > 120 for path in inputs)
    assert report["cases"][0]["legacy"][0]["status"] == "success"


def test_no_executable_reports_reference_and_parity_not_run(tmp_path, mocked_engines):
    settings = _settings(tmp_path, legacy_exe=None)
    report = validation.run_validation(settings)
    assert report["acceptance"]["native_coverage"] == "passed"
    assert report["acceptance"]["reference_coverage"] == "not_run"
    assert report["acceptance"]["same_grid_parity_by_model"]["legacy-2017"] == "not_run"
    assert report["cases"][0]["legacy"][0]["status"] == "not_run"
    assert not report["acceptance"]["validation_passed"]
    assert not mocked_engines


def test_skipped_numerical_tests_do_not_become_passing_from_parity(tmp_path, mocked_engines):
    report = validation.run_validation(_settings(tmp_path, run_numerical_tests=False))
    assert report["acceptance"]["same_grid_parity_by_model"]["legacy-2017"] == "passed"
    assert report["acceptance"]["numerical_tests"] == "not_run"
    assert not report["acceptance"]["validation_passed"]


def test_done_without_output_is_a_failed_reference(tmp_path, mocked_engines, monkeypatch):
    monkeypatch.setattr(validation, "_popen", lambda command, **options: FakeProcess(command, produce=False, **options))
    report = validation.run_validation(_settings(tmp_path))
    reference = report["cases"][0]["legacy"][0]
    assert reference["execution"]["status"] == "success"
    assert Path(reference["execution"]["stdout"]["path"]).read_bytes() == b"Done\r\n"
    assert reference["status"] == "failed"
    assert report["acceptance"]["reference_coverage"] == "failed"
    assert not report["acceptance"]["validation_passed"]


@pytest.mark.parametrize("mode", ["zero", "shorten"])
def test_empty_zero_or_truncated_reference_never_passes_coverage(tmp_path, mocked_engines, monkeypatch, mode):
    monkeypatch.setattr(
        validation, "_popen", lambda command, **options: FakeProcess(command, **{mode: True}, **options)
    )
    report = validation.run_validation(_settings(tmp_path))
    reference = report["cases"][0]["legacy"][0]
    assert reference["status"] == "partial"
    assert not reference["coverage"]["complete"]
    assert not report["acceptance"]["validation_passed"]


def test_nonzero_legacy_exit_invalidates_even_complete_output(tmp_path, mocked_engines, monkeypatch):
    monkeypatch.setattr(validation, "_popen", lambda command, **options: FakeProcess(command, returncode=7, **options))
    report = validation.run_validation(_settings(tmp_path))
    reference = report["cases"][0]["legacy"][0]
    assert reference["execution"]["returncode"] == 7
    assert reference["status"] == "failed"


def test_timeout_kills_child_and_retains_logs(tmp_path, mocked_engines, monkeypatch):
    processes = []

    class TimedOutProcess(FakeProcess):
        def communicate(self, timeout):
            self.communications += 1
            if self.communications == 1:
                raise subprocess.TimeoutExpired(self.command, timeout)
            return b"partial output", b"timed out"

    def launch(command, **options):
        process = TimedOutProcess(command, produce=False, **options)
        processes.append(process)
        return process

    monkeypatch.setattr(validation, "_popen", launch)
    report = validation.run_validation(_settings(tmp_path, timeout_seconds=0.01))
    reference = report["cases"][0]["legacy"][0]
    assert processes[0].killed
    assert processes[0].communications == 2
    assert reference["execution"]["status"] == "timeout"
    assert reference["status"] == "failed"
    assert Path(reference["execution"]["stderr"]["path"]).read_bytes() == b"timed out"


def test_fine_grids_are_native_only_and_never_interpolated(tmp_path, mocked_engines):
    settings = _settings(tmp_path, steps=(0.5, 0.05, 1 / 60))
    report = validation.run_validation(settings)
    assert len(mocked_engines) == 1
    cases = report["cases"][0]
    assert [run["coverage"]["actual_epochs"] for run in cases["native"]] == [48, 480, 1440]
    assert [run["status"] for run in cases["comparisons"]] == ["passed", "not_compared", "not_compared"]
    with pytest.raises(ValueError, match=r"below 0\.05"):
        validation.run_validation(replace(settings, output_dir=tmp_path / "unsafe", legacy_steps=(1 / 60,)))
    assert len(mocked_engines) == 1


def test_nan_candidate_and_sparse_failure_are_explicit(tmp_path, mocked_engines, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setattr(validation, "reconstruct", lambda data, config: _solution(config.time_step_hours, nan=True))
    report = validation.run_validation(settings)
    assert report["cases"][0]["native"][0]["status"] == "partial"
    assert report["acceptance"]["native_coverage"] == "failed"
    assert not report["acceptance"]["validation_passed"]

    def insufficient(data, config):
        raise ValueError("insufficient satellite geometry")

    monkeypatch.setattr(validation, "reconstruct", insufficient)
    failed = validation.run_validation(replace(settings, output_dir=tmp_path / "sparse"))
    assert failed["cases"][0]["native"][0]["error"] == "insufficient satellite geometry"
    assert not failed["acceptance"]["validation_passed"]


def test_missing_station_case_does_not_hide_coverage_failure(tmp_path, mocked_engines):
    report = validation.run_validation(_settings(tmp_path, days=(1, 2)))
    assert report["cases"][0]["input_status"] == "ready"
    assert report["cases"][1]["input_status"] == "failed"
    assert report["acceptance"]["native_coverage"] == "failed"
    assert report["acceptance"]["same_grid_parity_by_model"]["legacy-2017"] == "failed"


def test_all_seven_tec_coefficients_are_checked(tmp_path, mocked_engines, monkeypatch):
    def wrong_gradient(data, config):
        solution = _solution(config.time_step_hours)
        solution.rows[:, 7] += 0.01
        return solution

    monkeypatch.setattr(validation, "reconstruct", wrong_gradient)
    report = validation.run_validation(_settings(tmp_path))
    comparison = report["cases"][0]["comparisons"][0]
    assert comparison["tec"]["columns"]["I_v"]["passes_tolerance"]
    assert not comparison["tec"]["columns"]["G_q_t"]["passes_tolerance"]
    assert comparison["status"] == "failed"


def test_47_dcb_values_are_matched_by_key_and_missing_key_fails(tmp_path):
    biases = {
        **{f"G{index:02d}": -50 + index for index in range(1, 32)},
        **{f"R{index:02d}": -20 + index for index in range(1, 15)},
    }
    reference, candidate = tmp_path / "reference.dat", tmp_path / "candidate.dat"
    write_dcb_output(reference, biases)
    write_dcb_output(candidate, biases)
    report = validation.compare_dcb(reference, candidate, atol=0.001, rtol=0)
    assert report["passed"]
    assert report["matched_values"] == report["reference_values"] == 47
    content = candidate.read_text().splitlines()
    candidate.write_text("\n".join(line for line in content if not line.startswith("G 1 ")) + "\n")
    report = validation.compare_dcb(reference, candidate, atol=0.001, rtol=0)
    assert not report["passed"]
    assert report["missing_candidate_keys"] == ["satellites:G01"]


@pytest.mark.parametrize(
    "content",
    [
        "",
        "# DCB sat:\nG 1 nan\n# DCB rec:\nG 1\n",
        "# DCB sat:\nG 1 3\nG 1 4\n# DCB rec:\nG 1\n",
        "# DCB sat:\nG 1 3\n",
    ],
)
def test_invalid_or_incomplete_dcb_file_is_never_an_empty_pass(tmp_path, content):
    path = tmp_path / "bad.dat"
    path.write_text(content)
    with pytest.raises(ValueError, match=r"DCB|satellite"):
        validation.read_dcb(path)


def test_numerical_test_evidence_requires_tests_without_skips_or_failures(tmp_path, monkeypatch):
    processes = []

    class NumericalProcess(FakeProcess):
        def __init__(self, command, *, cwd, **options):
            super().__init__(command, cwd=cwd, produce=False, **options)
            xml = next(token.partition("=")[2] for token in command if token.startswith("--junitxml="))
            Path(xml).write_text('<testsuites><testsuite tests="3" failures="0" errors="0" skipped="1"/></testsuites>')
            processes.append(self)

    monkeypatch.setattr(validation, "_popen", NumericalProcess)
    report = validation._numerical_tests(tmp_path, 2)
    assert report["status"] == "failed"
    assert report["counts"]["skipped"] == 1
    assert len(processes) == 1
    assert any("test_absoltec_science.py" in item for item in processes[0].command)


def test_cli_fraction_steps_and_exit_code_for_missing_reference(tmp_path, mocked_engines, capsys):
    settings = _settings(tmp_path, legacy_exe=None)
    code = validation.main(
        [
            "--dat-path",
            str(settings.dat_path),
            "--output-dir",
            str(settings.output_dir),
            "--year",
            "2026",
            "--days",
            "001",
            "--sites",
            "mork",
            "--models",
            "legacy-2017",
            "--steps",
            "0.5,1/60",
        ]
    )
    assert code == 1
    assert "not_run" in capsys.readouterr().out
    report = json.loads((settings.output_dir / "report.json").read_text())
    assert report["cases"][0]["native"][1]["step_hours"] == 1 / 60
