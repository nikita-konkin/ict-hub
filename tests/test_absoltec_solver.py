"""Numerical recovery tests with independent signals, biases, and dense oracle.

No test adjusts the computed result to an expected TEC series. The exact
signal is one global quadratic, so it is represented exactly in every local
window, including windows between the generated observation times.
"""

from dataclasses import replace

import numpy as np
import pytest

from absoltec_native.io import read_dat
from absoltec_native.solver import (
    PreparedObservations,
    SolverConfig,
    SolverError,
    fit_prepared,
    prepare_observations,
    reconstruct,
)


def _exact_observations(*, noise=0.0):
    """Varied geometry and visibility with known, nonzero-mean link biases."""
    rng = np.random.default_rng(74502)
    satellite_ids = tuple(f"G{i:02d}" for i in range(1, 9)) + tuple(f"R{i:02d}" for i in range(1, 5))
    times = np.arange(-1.0, 25.01, 0.125)
    hour = np.repeat(times, len(satellite_ids))
    satellite = np.tile(np.arange(len(satellite_ids), dtype=np.int64), len(times))
    # Deliberate changing visibility; all links retain well-spread geometry
    # across windows. These are prepared design coordinates, not ephemerides.
    visible = (np.repeat(np.arange(len(times)), len(satellite_ids)) + satellite) % 7 != 0
    hour, satellite = hour[visible], satellite[visible]
    mapping = rng.uniform(1.05, 3.5, len(hour))
    dlon = rng.uniform(-7, 7, len(hour))
    dlat = rng.uniform(-8, 8, len(hour))
    bias = np.linspace(-17.0, -2.7, len(satellite_ids))
    t = hour - 12
    vertical = 30 + 0.08 * t + 0.015 * t**2 + 0.12 * dlon - 0.18 * dlat + 0.008 * dlon**2 + 0.012 * dlat**2
    slant = mapping * vertical + bias[satellite] + rng.normal(0, noise, len(hour))
    observations = PreparedObservations(hour, slant, mapping, dlon, dlat, satellite, satellite_ids, {})
    return observations, bias


def _expected_coefficients(hour, model):
    t = hour - 12
    derivative = 0.08 + 0.03 * t
    return np.column_stack(
        (
            30 + 0.08 * t + 0.015 * t**2,
            np.full(len(t), 0.12),
            np.full(len(t), -0.18),
            np.full(len(t), 0.008),
            np.full(len(t), 0.012),
            -derivative if model == "legacy-2017" else derivative,
            np.full(len(t), 0.015),
        )
    )


@pytest.mark.parametrize("model", ["legacy-2017", "published"])
@pytest.mark.parametrize("step", [0.5, 0.05, 1 / 60])
def test_recovers_every_coefficient_and_nonzero_mean_bias_on_fine_grids(model, step):
    observations, expected_bias = _exact_observations()
    solution = fit_prepared(observations, SolverConfig(time_step_hours=step, model=model))

    assert len(solution.rows) == round(24 / step)
    assert solution.diagnostics["solved_epochs"] == len(solution.rows)
    assert np.all(np.isfinite(solution.rows))
    np.testing.assert_allclose(solution.rows[:, 0], np.arange(round(24 / step)) * step, atol=1e-12)
    np.testing.assert_allclose(solution.rows[:, 1:], _expected_coefficients(solution.rows[:, 0], model), atol=2e-9)
    np.testing.assert_allclose(list(solution.combined_bias.values()), expected_bias, atol=2e-9)
    # A fabricated zero-mean constraint would break these recoveries.
    assert np.mean(list(solution.combined_bias.values())) < -5
    assert solution.diagnostics["scientific_parity"] == "unverified"


def test_satellite_budget_prevents_unbounded_reduced_system():
    observations, _ = _exact_observations()
    with pytest.raises(SolverError, match="resource budget"):
        fit_prepared(observations, SolverConfig(max_satellites=11))


def _dense_day_oracle(observations, model, *, excluded_boundary="both"):
    """Solve weighted slant-space rows directly, without Schur elimination.

    Five fit epochs (including internal 24h) keep this oracle intentionally
    small. Observation timestamps are shared by several satellites, testing
    the strict time-window boundary on every row at that timestamp.
    """
    epochs = np.asarray([0.0, 6.0, 12.0, 18.0, 24.0])
    satellite_count = len(observations.satellite_ids)
    blocks, targets = [], []
    for k, center in enumerate(epochs):
        lower = observations.hour > center - 1 if excluded_boundary == "both" else observations.hour >= center - 1
        selected = lower & (observations.hour < center + 1)
        hour = observations.hour[selected]
        dt = hour - center if model == "published" else center - hour
        dl, db, mapping = observations.dlon[selected], observations.dlat[selected], observations.mapping[selected]
        row_count = len(hour)
        matrix = np.zeros((row_count, 7 * len(epochs) + satellite_count))
        polynomial = np.column_stack((np.ones(row_count), dl, db, dl**2, db**2, dt, dt**2))
        matrix[:, 7 * k : 7 * (k + 1)] = mapping[:, None] * polynomial
        matrix[np.arange(row_count), 7 * len(epochs) + observations.satellite[selected]] = 1
        weight_root = 1 / np.sqrt(mapping * (1 + dt**2))
        blocks.append(matrix * weight_root[:, None])
        targets.append(observations.slant_tec[selected] * weight_root)
    matrix = np.vstack(blocks)
    vector, _, rank, _ = np.linalg.lstsq(matrix, np.concatenate(targets), rcond=None)
    assert rank == matrix.shape[1], "The independent oracle fixture must be identifiable"
    return vector[: 7 * len(epochs)].reshape(-1, 7)[:4], vector[7 * len(epochs) :]


@pytest.mark.parametrize("model", ["legacy-2017", "published"])
def test_schur_solution_matches_independent_noisy_dense_weighted_fit(model):
    observations, _ = _exact_observations(noise=0.15)
    expected_coefficients, expected_bias = _dense_day_oracle(observations, model)
    # Verify the fixture actually distinguishes the boundary rule.
    inclusive_coefficients, _ = _dense_day_oracle(observations, model, excluded_boundary="upper")
    assert np.max(np.abs(expected_coefficients - inclusive_coefficients)) > 1e-4

    solution = fit_prepared(observations, SolverConfig(time_step_hours=6, model=model))
    np.testing.assert_allclose(solution.rows[:, 1:], expected_coefficients, rtol=1e-9, atol=2e-9)
    np.testing.assert_allclose(list(solution.combined_bias.values()), expected_bias, rtol=1e-9, atol=2e-9)


def test_degenerate_geometry_raises_without_publishing_fabricated_zero_values():
    observations, _ = _exact_observations()
    degenerate = replace(observations, dlon=np.zeros_like(observations.hour), dlat=np.zeros_like(observations.hour))
    with pytest.raises(SolverError, match=r"full-rank|degenerate"):
        fit_prepared(degenerate, SolverConfig())


def test_constant_mapping_does_not_hide_unidentifiable_bias_offset():
    observations, _ = _exact_observations()
    unidentifiable = replace(observations, mapping=np.ones_like(observations.hour))
    with pytest.raises(SolverError, match=r"rank-deficient|identifiable"):
        fit_prepared(unidentifiable, SolverConfig(time_step_hours=6))


def test_unsupported_epochs_remain_nan_gaps():
    observations, _ = _exact_observations()
    selected = observations.hour < 10
    truncated = replace(
        observations,
        **{
            name: getattr(observations, name)[selected]
            for name in ("hour", "slant_tec", "mapping", "dlon", "dlat", "satellite")
        },
    )
    solution = fit_prepared(truncated, SolverConfig())
    assert np.any(np.isfinite(solution.rows[:, 1]))
    assert np.all(np.isnan(solution.rows[solution.rows[:, 0] >= 11, 1:]))
    assert solution.diagnostics["solved_epochs"] < len(solution.rows)


def _write_satellite(tmp_path, phase, code, *, validity=None, hour=None, satellite="G01"):
    phase, code = np.asarray(phase), np.asarray(code)
    validity = np.zeros(len(phase), dtype=int) if validity is None else np.asarray(validity)
    hour = np.arange(len(phase)) / 120 if hour is None else np.asarray(hour)
    cadence = (hour[1] - hour[0]) * 3600 if len(hour) > 1 else 30
    path = tmp_path / f"test_{satellite}_001_2026.dat"
    header = (
        f"# Satellite: {satellite}\n# Site: test\n# Interval: {cadence:.12g}\n"
        "# Position (L, B, H): 12, 45, 100\n"
        "# Columns: tsn, hour, el, az, tec.l1l2, tec.c1p2, validity\n"
    )
    rows = "".join(
        f"{i} {t:.15g} 50 120 {p:.15g} {c:.15g} {v}\n"
        for i, (t, p, c, v) in enumerate(zip(hour, phase, code, validity, strict=True))
    )
    path.write_text(header + rows, encoding="utf-8")
    return read_dat(path)


def test_leveling_preserves_valid_zero_phase_without_reference_calibration(tmp_path):
    phase = np.arange(8, dtype=float)
    data = _write_satellite(tmp_path, phase, phase + 10)
    prepared = prepare_observations([data], SolverConfig(model="published", min_arc_samples=3))
    np.testing.assert_allclose(prepared.slant_tec, phase + 10)
    assert prepared.hour[0] == 0
    assert prepared.diagnostics["missing_phase"] == 0
    assert prepared.diagnostics["accepted_arcs"] == 1


def test_code_missing_sample_stays_in_continuous_phase_arc(tmp_path):
    phase = np.arange(8, dtype=float)
    code = phase + 10
    code[3] = 0  # Invalid placeholder must not contaminate the leveling mean.
    validity = np.zeros(8, dtype=int)
    validity[3] = 68
    data = _write_satellite(tmp_path, phase, code, validity=validity)
    prepared = prepare_observations([data], SolverConfig(model="published", min_arc_samples=3))
    np.testing.assert_allclose(prepared.slant_tec, phase + 10)
    assert len(prepared.hour) == len(phase)
    assert prepared.diagnostics["accepted_arcs"] == 1
    assert prepared.diagnostics["missing_code"] == 1


def test_cycle_slip_levels_each_side_independently(tmp_path):
    true_slant = 30 + np.arange(12) * 0.1
    phase = true_slant - np.repeat([100.0, 200.0], 6)
    validity = np.zeros(12, dtype=int)
    validity[6] = 1
    data = _write_satellite(tmp_path, phase, true_slant, validity=validity)
    prepared = prepare_observations([data], SolverConfig(model="published", min_arc_samples=4))
    np.testing.assert_allclose(prepared.slant_tec, true_slant, atol=1e-12)
    assert prepared.diagnostics["accepted_arcs"] == 2


def test_missing_phase_excludes_sample_and_splits_both_sides(tmp_path):
    phase = np.arange(8, dtype=float)
    validity = np.zeros(8, dtype=int)
    validity[3] = 24
    data = _write_satellite(tmp_path, phase, phase + 10, validity=validity)
    prepared = prepare_observations([data], SolverConfig(model="published", min_arc_samples=3))
    expected_indices = [0, 1, 2, 4, 5, 6, 7]
    np.testing.assert_allclose(prepared.hour, data.hour[expected_indices])
    np.testing.assert_allclose(prepared.slant_tec, phase[expected_indices] + 10)
    assert prepared.diagnostics["accepted_arcs"] == 2
    assert prepared.diagnostics["missing_phase"] == 1


def test_no_legacy_thirty_second_sampling_gate(tmp_path):
    phase = np.arange(10, dtype=float)
    data = _write_satellite(tmp_path, phase, phase + 10, hour=np.arange(10) / 3600)
    prepared = prepare_observations([data], SolverConfig(model="published", min_arc_samples=3))
    assert len(prepared.hour) == 10
    np.testing.assert_allclose(prepared.slant_tec, phase + 10)


def test_insufficient_code_does_not_invent_leveling_constant(tmp_path):
    phase = np.arange(8, dtype=float)
    validity = np.full(8, 68)
    validity[0] = 0
    data = _write_satellite(tmp_path, phase, phase + 10, validity=validity)
    with pytest.raises(SolverError, match="valid code"):
        prepare_observations([data], SolverConfig(model="published", min_arc_samples=3))


def test_observation_budget_is_checked_before_geometry_allocation(tmp_path):
    phase = np.arange(8, dtype=float)
    data = _write_satellite(tmp_path, phase, phase + 10)
    with pytest.raises(SolverError, match="observation budget"):
        prepare_observations([data], SolverConfig(model="published", max_observations=7))


def test_empty_input_has_explicit_error():
    with pytest.raises(SolverError, match="No satellite"):
        reconstruct([])


@pytest.mark.parametrize(
    "arguments",
    [
        {"time_step_hours": 0},
        {"time_step_hours": np.nan},
        {"time_step_hours": 25},
        {"time_step_hours": 1 / 3600},
        {"elevation_cutoff": 90},
        {"correction_coefficient": 0},
        {"window_hours": np.inf},
        {"min_arc_samples": True},
        {"min_satellites": 0},
        {"max_observations": -1},
        {"condition_limit": 1},
        {"model": "unknown"},
    ],
)
def test_invalid_or_over_budget_configuration_fails_before_fitting(arguments):
    with pytest.raises(ValueError, match=r"must|budget"):
        SolverConfig(**arguments)
