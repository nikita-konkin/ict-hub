"""Behavioral probes for recovered legacy quirks, without a reference fit."""

import numpy as np
import pytest

from absoltec_native.io import read_dat
from absoltec_native.legacy import prepare_legacy
from absoltec_native.solver import SolverConfig, SolverError


def _legacy_input(tmp_path, *, count=400, phase=None, code=None, validity=0):
    phase = 10 + np.arange(count) * 0.01 if phase is None else np.asarray(phase)
    code = phase + 5 if code is None else np.asarray(code)
    path = tmp_path / "test_G01_001_2026.dat"
    path.write_text(
        "# Satellite: G01\n# Site: test\n# Interval: 30\n"
        "# Position (L, B, H): 60,55,0\n"
        "# Columns: tsn, hour, el, az, tec.l1l2, tec.c1p2, validity\n"
        + "".join(
            f"{i} {i / 120:.15g} 45 90 {p:.15g} {c:.15g} {validity}\n"
            for i, (p, c) in enumerate(zip(phase, code, strict=True))
        ),
        encoding="utf-8",
    )
    return read_dat(path)


def test_recovered_two_sample_shift_and_initial_geometry_slot(tmp_path):
    data = _legacy_input(tmp_path)
    observations = prepare_legacy([data], SolverConfig())
    # 0x40848a starts leveled source at start+2; geometry slot1 stays zero,
    # so its first usable pair is time index1 and code/phase index3.
    np.testing.assert_allclose(observations.hour, data.hour[1:-2])
    np.testing.assert_allclose(observations.slant_tec, data.code_tec[3:], atol=1e-12)
    assert observations.diagnostics["legacy_phase_shifts"] == 0


def test_compatibility_mapping_preserves_observed_radius_literal(tmp_path):
    observations = prepare_legacy([_legacy_input(tmp_path)], SolverConfig())
    angle = np.radians(0.97 * (90 - 45))
    expected = 1 / np.sqrt(1 - (6371302 / (6371302 + 450) * np.sin(angle)) ** 2)
    np.testing.assert_allclose(observations.mapping, expected, atol=1e-14)


def test_legacy_ignores_validity_flags_with_nonzero_numeric_measurements(tmp_path):
    data = _legacy_input(tmp_path, validity=24)
    assert not np.any(data.phase_valid)
    observations = prepare_legacy([data], SolverConfig())
    assert len(observations.hour) == 397
    assert observations.diagnostics["legacy_validity_flags_ignored"] is True


def test_legacy_zero_phase_is_missing_even_with_clear_validity_flags(tmp_path):
    data = _legacy_input(tmp_path, phase=np.zeros(400), code=np.full(400, 5))
    assert np.all(data.phase_valid)
    with pytest.raises(SolverError, match="No legacy-compatible arcs"):
        prepare_legacy([data], SolverConfig())


def test_legacy_retains_fixed_sample_capacity_explicitly(tmp_path):
    observations = prepare_legacy([_legacy_input(tmp_path, count=2200)], SolverConfig())
    assert observations.diagnostics["legacy_retained_input_observations"] == 1999
    assert observations.diagnostics["legacy_cap_truncated_files"] == 1
    assert len(observations.hour) == 1996


def test_isolated_phase_spike_is_replaced_by_previous_phase(tmp_path):
    baseline = 10 + np.arange(400) * 0.01
    phase = baseline.copy()
    phase[200] += 20
    data = _legacy_input(tmp_path, phase=phase, code=baseline + 5)
    observations = prepare_legacy([data], SolverConfig())
    expected = baseline + 5
    expected[200] = expected[199]
    np.testing.assert_allclose(observations.slant_tec, expected[3:], atol=1e-11)
    assert observations.diagnostics["legacy_phase_spikes"] == 1
    assert observations.diagnostics["legacy_code_spikes"] == 1


def test_sustained_phase_slip_shifts_tail_before_arc_leveling(tmp_path):
    baseline = 10 + np.arange(400) * 0.01
    phase = baseline.copy()
    phase[200:] += 20
    data = _legacy_input(tmp_path, phase=phase, code=baseline + 5)
    observations = prepare_legacy([data], SolverConfig())
    # The recovered shift makes the slip's first phase equal its predecessor,
    # retaining a .01 step error. Leveling distributes it around the arc mean.
    expected = baseline + 5.005
    expected[200:] -= 0.01
    np.testing.assert_allclose(observations.slant_tec, expected[3:], atol=1e-11)
    assert observations.diagnostics["legacy_phase_shifts"] == 1


def test_window_eligibility_honors_window_width_and_sample_threshold(tmp_path):
    data = _legacy_input(tmp_path)
    config = SolverConfig(window_hours=0.1, min_window_samples=20)
    observations = prepare_legacy([data], config)
    epochs = np.arange(49) * 0.5
    expected = np.asarray([np.count_nonzero((data.hour >= t - 0.1) & (data.hour <= t + 0.1)) >= 20 for t in epochs])
    np.testing.assert_array_equal(observations.window_satellite_mask[:, 0], expected)
    with pytest.raises(SolverError, match="No legacy-compatible arcs"):
        prepare_legacy([data], SolverConfig(window_hours=0.1, min_window_samples=100))


def test_custom_arc_span_threshold_is_not_silently_ignored(tmp_path):
    data = _legacy_input(tmp_path)
    with pytest.raises(SolverError, match="No legacy-compatible arcs"):
        prepare_legacy([data], SolverConfig(min_arc_samples=500))
