"""The experimental CLI must isolate failures and publish station results together."""

import hashlib
import json
from dataclasses import dataclass
from types import SimpleNamespace

import numpy as np
import pytest

from absoltec_native import __main__ as cli


@dataclass
class FakeConfig:
    time_step_hours: float = 0.5
    elevation_cutoff: float = 10.0
    correction_coefficient: float = 0.97
    model: str = "legacy-2017"
    window_hours: float = 1.0
    min_arc_samples: int = 21
    min_window_samples: int = 20
    min_satellites: int = 6


def _input(root, *, site="mork", satellite="G01", day=1, filename_year="26"):
    folder = root / "2026" / f"{day:03d}" / site
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{site[:4]}_{satellite}_{day:03d}_{filename_year}.dat"
    path.write_text(
        f"# Satellite: {satellite}\n# Site: {site[:4]}\n# Position (L, B, H): 48.0, 56.0, 100\n"
        "# Interval: 1800\n# Columns: tsn, hour, el, az, tec.l1l2, tec.c1p2, validity\n"
        "1 0 45 90 15 20 0\n2 0.5 45 90 16 21 0\n",
        encoding="ascii",
    )
    return path


def _solution(*, partial=False):
    rows = np.array([[0, 10, 1, 2, 3, 4, 5, 6], [0.5, 20, 2, 3, 4, 5, 6, 7]], dtype=float)
    if partial:
        rows[1, 1:] = np.nan
    return SimpleNamespace(rows=rows, combined_bias={"G01": 1.25}, diagnostics={"synthetic": True})


@pytest.fixture
def solver(monkeypatch):
    calls = []

    def fake_reconstruct(data, config):
        calls.append((data, config))
        return _solution()

    monkeypatch.setattr(
        cli,
        "_build_config",
        lambda args: FakeConfig(**{name: getattr(args, name) for name in FakeConfig.__dataclass_fields__}),
    )
    monkeypatch.setattr(cli, "reconstruct", fake_reconstruct)
    return calls


def _args(source, output, *extra):
    return ["--dat-path", str(source), "--output-dir", str(output), "--year", "2026", "--day-of-year", "1", *extra]


def test_success_records_input_identity_config_and_unverified_science(tmp_path, solver, capsys):
    source, output = tmp_path / "in", tmp_path / "out"
    path = _input(source, site="mork0010")
    assert cli.main(_args(source, output, "--site", "mork0010", "--time-step-hours", "0.05")) == 0
    destination = output / "2026" / "001" / "mork0010"
    assert {path.name for path in destination.iterdir()} == {
        "mork_001_2026.dat",
        "DCB_mork_001_2026.dat",
        "DCB_mork_001_2026.json",
        "diagnostics.json",
    }
    diagnostics = json.loads((destination / "diagnostics.json").read_text())
    assert diagnostics["status"] == "success"
    assert diagnostics["scientific_parity"] == "unverified"
    assert diagnostics["config"]["time_step_hours"] == 0.05
    assert diagnostics["inputs"][0]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert diagnostics["runtime_seconds"] >= 0
    assert len(solver) == 1
    assert "Processing year=2026 day=001 site=mork0010 (1/1)" in capsys.readouterr().out
    bias = json.loads((destination / "DCB_mork_001_2026.json").read_text())
    assert bias["combined_bias"] == {"G01": 1.25}
    assert "not separate legacy DCB" in bias["description"]


def test_existing_output_is_refused_before_computing_and_overwrite_replaces_whole_station(tmp_path, solver):
    source, output = tmp_path / "in", tmp_path / "out"
    _input(source)
    args = _args(source, output)
    assert cli.main(args) == 0
    destination = output / "2026" / "001" / "mork"
    previous = (destination / "diagnostics.json").read_bytes()
    (destination / "old-file.txt").write_text("old")
    assert cli.main(args) == 1
    assert len(solver) == 1
    assert (destination / "diagnostics.json").read_bytes() == previous
    assert cli.main([*args, "--overwrite"]) == 0
    assert len(solver) == 2
    assert not (destination / "old-file.txt").exists()
    assert not list(destination.parent.glob(".mork.*"))


def test_failed_station_keeps_existing_results_and_other_station_runs(tmp_path, solver, monkeypatch):
    source, output = tmp_path / "in", tmp_path / "out"
    _input(source, site="mork")
    _input(source, site="ntor")
    prior = output / "2026" / "001" / "mork"
    prior.mkdir(parents=True)
    (prior / "prior.dat").write_text("valid prior output")

    def solve(data, config):
        if data[0].station == "mork":
            raise ValueError("singular observations")
        return _solution()

    monkeypatch.setattr(cli, "reconstruct", solve)
    assert cli.main(_args(source, output, "--overwrite", "--jobs", "2")) == 1
    assert (prior / "prior.dat").read_text() == "valid prior output"
    assert (output / "2026" / "001" / "ntor" / "diagnostics.json").exists()
    failures = list((output / "_failures" / "2026" / "001").glob("mork-*.json"))
    assert len(failures) == 1
    assert json.loads(failures[0].read_text())["error"] == "singular observations"


def test_partial_output_stays_explicitly_missing_and_returns_failure(tmp_path, solver, monkeypatch):
    source, output = tmp_path / "in", tmp_path / "out"
    _input(source)
    monkeypatch.setattr(cli, "reconstruct", lambda data, config: _solution(partial=True))
    assert cli.main(_args(source, output)) == 1
    destination = output / "2026" / "001" / "mork"
    diagnostics = json.loads((destination / "diagnostics.json").read_text())
    assert diagnostics["status"] == "partial"
    assert diagnostics["solved_epochs"] == 1
    assert diagnostics["total_epochs"] == 2
    assert "nan" in (destination / "mork_001_2026.dat").read_text()


def test_write_failure_never_publishes_a_partial_station_directory(tmp_path, solver, monkeypatch):
    source, output = tmp_path / "in", tmp_path / "out"
    _input(source)

    def broken_write(path, rows):
        path.write_text("incomplete data")
        raise OSError("simulated disk write failure")

    monkeypatch.setattr(cli, "write_tec_output", broken_write)
    assert cli.main(_args(source, output)) == 1
    parent = output / "2026" / "001"
    assert not (parent / "mork").exists()
    assert not list(parent.glob(".mork.*"))
    assert list((output / "_failures" / "2026" / "001").glob("mork-*.json"))


def test_failed_directory_publish_restores_old_station_transaction(tmp_path, solver, monkeypatch):
    source, output = tmp_path / "in", tmp_path / "out"
    _input(source)
    args = _args(source, output)
    assert cli.main(args) == 0
    destination = output / "2026" / "001" / "mork"
    previous = (destination / "diagnostics.json").read_bytes()
    real_replace = cli.os.replace

    def fail_publication(source_path, target_path):
        if ".stage-" in source_path.name and target_path == destination:
            raise OSError("simulated publish failure")
        return real_replace(source_path, target_path)

    monkeypatch.setattr(cli.os, "replace", fail_publication)
    assert cli.main([*args, "--overwrite"]) == 1
    assert (destination / "diagnostics.json").read_bytes() == previous
    assert not list(destination.parent.glob(".mork.*"))


def test_another_transaction_lock_is_respected_and_never_removed(tmp_path, solver):
    source, output = tmp_path / "in", tmp_path / "out"
    _input(source)
    parent = output / "2026" / "001"
    parent.mkdir(parents=True)
    lock = parent / ".mork.lock"
    lock.write_text("another process owns this lock")
    assert cli.main(_args(source, output)) == 1
    assert lock.read_text() == "another process owns this lock"
    assert not solver


def test_unsupported_constellations_are_excluded_and_counted(tmp_path, solver):
    source, output = tmp_path / "in", tmp_path / "out"
    _input(source)
    _input(source, satellite="E01")
    assert cli.main(_args(source, output)) == 0
    assert len(solver[0][0]) == 1
    diagnostics = json.loads((output / "2026" / "001" / "mork" / "diagnostics.json").read_text())
    assert diagnostics["excluded_unsupported_constellations"] == {"files": 1, "observations": 2, "satellites": ["E01"]}


def test_bad_input_and_two_digit_year_mismatch_are_recorded_as_failure(tmp_path, solver):
    source, output = tmp_path / "in", tmp_path / "out"
    _input(source, filename_year="25")
    assert cli.main(_args(source, output)) == 1
    assert not solver
    failure = next((output / "_failures" / "2026" / "001").glob("*.json"))
    assert "filename year" in json.loads(failure.read_text())["error"]


def test_days_ranges_discover_sites_and_continue_after_missing_day(tmp_path, solver):
    source, output = tmp_path / "in", tmp_path / "out"
    _input(source, day=1)
    _input(source, day=3)
    args = ["--dat-path", str(source), "--output-dir", str(output), "--year", "2026", "--days", "001-003"]
    assert cli.main(args) == 1
    assert len(solver) == 2
    assert (output / "2026" / "001" / "mork" / "diagnostics.json").exists()
    assert (output / "2026" / "003" / "mork" / "diagnostics.json").exists()
    assert list((output / "_failures" / "2026" / "002").glob("all-stations-*.json"))


@pytest.mark.parametrize(
    "extra",
    [
        ["--site", "../mork"],
        ["--site", "mork,"],
        ["--time-step-hours", "0"],
        ["--jobs", "9"],
        ["--elevation-cutoff", "90"],
    ],
)
def test_invalid_options_fail_before_creating_output(tmp_path, solver, extra):
    source, output = tmp_path / "in", tmp_path / "out"
    _input(source)
    with pytest.raises(SystemExit, match="2"):
        cli.main(_args(source, output, *extra))
    assert not output.exists()


def test_overlapping_input_output_roots_are_rejected(tmp_path, solver):
    source = tmp_path / "in"
    _input(source)
    with pytest.raises(SystemExit, match="2"):
        cli.main(_args(source, source))


def test_day_366_is_rejected_for_nonleap_year(tmp_path, solver):
    source = tmp_path / "in"
    source.mkdir()
    args = ["--dat-path", str(source), "--output-dir", str(tmp_path / "out"), "--year", "2026", "--days", "366"]
    with pytest.raises(SystemExit, match="2"):
        cli.main(args)


def test_help_explains_experimental_status_without_importing_solver(capsys):
    with pytest.raises(SystemExit, match="0"):
        cli.main(["--help"])
    assert "scientific parity is unverified" in " ".join(capsys.readouterr().out.split())


def test_published_upgrade_flags_reach_solver_config():
    args = cli._parser().parse_args(
        [
            "--dat-path",
            "in",
            "--output-dir",
            "out",
            "--year",
            "2026",
            "--day-of-year",
            "1",
            "--model",
            "published",
            "--auto-alpha",
            "--shell-height-km",
            "506.7",
            "--nonneg",
        ]
    )
    config = cli._build_config(args)
    assert config.model == "published"
    assert config.auto_mapping_alpha is True
    assert config.mapping_shell_height_km == 506.7
    assert config.nonneg is True


@pytest.mark.parametrize("flag", [["--nonneg"], ["--auto-alpha"], ["--shell-height-km", "500"]])
def test_legacy_model_rejects_published_upgrade_flags(tmp_path, capsys, flag):
    source = tmp_path / "in"
    source.mkdir()
    args = ["--dat-path", str(source), "--output-dir", str(tmp_path / "out"), "--year", "2026", "--day-of-year", "1", *flag]
    with pytest.raises(SystemExit, match="2"):
        cli.main(args)
    assert "legacy" in capsys.readouterr().err
