"""Independent model-consistency checks for the published native TEC route.

Truth comes from Cartesian ray/shell intersections and an analytic physical
field, never from the implementation's angular geometry or a fitted reference
correction. These tests establish numerical/model consistency, not agreement
with the real ionosphere or certification of a scientific data product.
"""

import re

import numpy as np
import pytest

from absoltec_native.bias import split_biases, write_dcb_output
from absoltec_native.geometry import mapping_factor, pierce_point
from absoltec_native.io import read_dat, read_tec_output, write_tec_output
from absoltec_native.solver import (
    PreparedObservations,
    SolverConfig,
    SolverError,
    fit_prepared,
    prepare_observations,
    reconstruct,
)

_RADIUS_KM = 6371.0
_SHELL_KM = 450.0
_SPATIAL = np.asarray([0.025, -0.04, 0.0005, 0.001])


def _cartesian_truth(elevation, azimuth, station_latitude, station_longitude, correction):
    """Intersect an ENU ray with a sphere, using only Cartesian operations."""
    phi, lam = np.radians([station_latitude, station_longitude])
    up = np.asarray([np.cos(phi) * np.cos(lam), np.cos(phi) * np.sin(lam), np.sin(phi)])
    east = np.asarray([-np.sin(lam), np.cos(lam), 0.0])
    north = np.cross(up, east)
    origin = _RADIUS_KM * up
    azimuth = np.radians(azimuth)

    def intersect(ray_elevation):
        ray_elevation = np.radians(ray_elevation)
        direction = (
            np.sin(ray_elevation)[..., None] * up
            + (np.cos(ray_elevation) * np.cos(azimuth))[..., None] * north
            + (np.cos(ray_elevation) * np.sin(azimuth))[..., None] * east
        )
        along = direction @ origin
        distance = -along + np.sqrt(along**2 + (_RADIUS_KM + _SHELL_KM) ** 2 - origin @ origin)
        intersection = origin + distance[..., None] * direction
        return intersection, direction

    point, _ = intersect(elevation)
    latitude = np.degrees(np.arctan2(point[..., 2], np.hypot(point[..., 0], point[..., 1])))
    longitude = np.degrees(np.arctan2(point[..., 1], point[..., 0]))
    # The modified mapping uses a corrected zenith angle. Its slant factor
    # follows from projecting the ray onto the intersected shell's normal.
    modified, direction = intersect(90 - correction * (90 - np.asarray(elevation)))
    normal = modified / np.linalg.norm(modified, axis=-1)[..., None]
    mapping = 1 / np.sum(normal * direction, axis=-1)
    longitude_difference = np.degrees(np.angle(np.exp(1j * np.radians(longitude - station_longitude))))
    return latitude, longitude, mapping, longitude_difference, latitude - station_latitude


def _vertical_field(hour, dlon, dlat):
    time = np.asarray(hour) - 12
    gx, gy, qx, qy = _SPATIAL
    return 24 + 0.1 * time + 0.006 * time**2 + gx * dlon + gy * dlat + qx * dlon**2 + qy * dlat**2


def _expected_coefficients(hour):
    time = np.asarray(hour) - 12
    return np.column_stack(
        (
            24 + 0.1 * time + 0.006 * time**2,
            *[np.full(len(time), value) for value in _SPATIAL],
            0.1 + 0.012 * time,
            np.full(len(time), 0.006),
        )
    )


def _satellite_ids(constellation):
    if constellation == "mixed":
        return tuple(f"G{i:02d}" for i in range(1, 7)) + tuple(f"R{i:02d}" for i in range(1, 7))
    return tuple(f"{constellation}{i:02d}" for i in range(1, 13))


def _make_day(
    directory,
    *,
    latitude=52.0,
    longitude=60.0,
    correction=0.97,
    constellation="mixed",
    cadence_seconds=120,
    ambiguity_shift=0.0,
    link_shifts=None,
    code_noise=0.0,
    phase_noise=0.0,
    code_combination="tec.c1p2",
    zero_phase=False,
    missing_phase=False,
    gap=None,
    geometry="moving",
):
    """Write authentic DAT columns before calling the production parser.

    Sky tracks vary smoothly; they are a controlled coverage experiment,
    not a claim to reproduce satellite ephemerides. Every satellite has
    independent ambiguities before 08h, 08-16h, and after 16h; some code
    measurements are missing but their carrier phase remains available.
    """
    directory.mkdir(parents=True, exist_ok=True)
    ids = _satellite_ids(constellation)
    seconds = np.arange(0, 86400, cadence_seconds, dtype=np.int64)
    if gap is not None:
        hour = seconds / 3600
        seconds = seconds[(hour < gap[0]) | (hour >= gap[1])]
    hour = seconds / 3600
    arc = np.searchsorted([8.0, 16.0], hour, side="right")
    rng = np.random.default_rng(20261117)
    combined_bias = {}
    files = []
    crosses_dateline = crosses_pole = False
    for j, satellite in enumerate(ids):
        phase_angle = 2 * np.pi * (hour / (6 + 0.07 * j) + j / len(ids))
        elevation = 17 + 61 * (0.5 + 0.5 * np.sin(phase_angle))
        azimuth = (30 * j + 14 * hour + 20 * np.sin(0.29 * hour + j)) % 360
        if geometry in {"constant", "nearly-constant"}:
            amplitude = 0 if geometry == "constant" else 1e-9
            elevation = np.full(len(hour), 45.0) + amplitude * np.sin(phase_angle)
            azimuth = np.full(len(hour), 90.0) + amplitude * np.cos(phase_angle)
        _lat, lon, factor, dl, db = _cartesian_truth(elevation, azimuth, latitude, longitude, correction)
        crosses_dateline |= bool(np.any(np.abs(lon - longitude) > 180))
        crosses_pole |= bool(np.any(np.abs(dl) > 90))
        shift = 0.0 if link_shifts is None else float(link_shifts[j])
        bias = 6.0 + 0.35 * j + shift
        combined_bias[satellite] = bias
        true_slant = factor * _vertical_field(hour, dl, db) + bias
        ambiguity = np.asarray([40 + 3 * j, 90 - 2 * j, -30 + 5 * j])[arc]
        ambiguity = ambiguity + ambiguity_shift * (1 + j * 0.1 + arc * 0.03)
        if zero_phase:
            ambiguity = ambiguity.copy()
            ambiguity[arc == 0] = true_slant[0]
        phase = true_slant - ambiguity + rng.normal(0, phase_noise, len(hour))
        code = true_slant + rng.normal(0, code_noise, len(hour))
        validity = np.zeros(len(hour), dtype=int)
        missing_code = (np.arange(len(hour)) + j) % 13 == 4
        code[missing_code] = 0
        validity[missing_code] |= 68 if code_combination == "tec.c1p2" else 96
        validity[(seconds == 8 * 3600) | (seconds == 16 * 3600)] |= 1
        if missing_phase:
            lost_phase = (hour >= 2) & (hour < 2 + 4 * cadence_seconds / 3600)
            phase[lost_phase] = 0
            validity[lost_phase] |= 24
        path = directory / f"test_{satellite}_001_2026.dat"
        channel = f" (k = {j % 14 - 7})" if satellite.startswith("R") else ""
        header = (
            f"# Satellite: {satellite}{channel}\n# Site: test\n# Interval: {cadence_seconds}\n"
            f"# Position (L, B, H): {longitude:.16g}, {latitude:.16g}, 100\n"
            f"# Columns: tsn, hour, el, az, tec.l1l2, {code_combination}, validity\n"
        )
        rows = "".join(
            f"{int(s // cadence_seconds)} {h:.16g} {e:.16g} {a:.16g} {p:.16g} {c:.16g} {v}\n"
            for s, h, e, a, p, c, v in zip(seconds, hour, elevation, azimuth, phase, code, validity, strict=True)
        )
        path.write_text(header + rows, encoding="ascii")
        files.append(path)
    return files, combined_bias, {"dateline": crosses_dateline, "pole": crosses_pole}


def _read_dcb(path):
    satellites, receivers = {}, {}
    section = None
    for line in path.read_text(encoding="ascii").splitlines():
        if line.startswith("#"):
            section = "satellite" if line == "# DCB sat:" else "receiver"
            continue
        if section == "satellite":
            match = re.fullmatch(r"([GR])\s*(\d+)\s+([-+\d.eE]+)", line)
            assert match, line
            satellites[f"{match[1]}{int(match[2]):02d}"] = float(match[3])
        else:
            name, value = line.split()
            receivers[name] = float(value)
    return satellites, receivers


@pytest.mark.parametrize(
    ("latitude", "longitude", "correction", "constellation", "crossing"),
    [
        (0.0, 100.0, 0.87, "G", None),
        (52.0, 60.0, 0.97, "mixed", None),
        (76.5, -68.8, 0.94, "R", None),
        (45.0, 179.8, 0.97, "mixed", "dateline"),
        (88.7, 25.0, 0.94, "mixed", "pole"),
    ],
)
def test_cartesian_generated_day_recovers_all_seven_parameters_and_dcb_files(
    tmp_path, latitude, longitude, correction, constellation, crossing
):
    files, expected_bias, crossings = _make_day(
        tmp_path / "input", latitude=latitude, longitude=longitude, correction=correction, constellation=constellation
    )
    if crossing is not None:
        assert crossings[crossing], "Fixture must actually cross the coordinate discontinuity"
    data = [read_dat(path) for path in files]
    solution = reconstruct(data, SolverConfig(model="published", correction_coefficient=correction))
    assert solution.diagnostics["solved_epochs"] == 48
    assert solution.diagnostics["accepted_arcs"] == 3 * len(data)
    assert solution.diagnostics["missing_code"] > 0
    np.testing.assert_allclose(solution.rows[:, 1:], _expected_coefficients(solution.rows[:, 0]), rtol=0, atol=2e-7)
    for name, expected in expected_bias.items():
        assert solution.combined_bias[name] == pytest.approx(expected, abs=2e-7)

    output = tmp_path / "test_001_2026.dat"
    write_tec_output(output, solution.rows)
    np.testing.assert_allclose(read_tec_output(output), solution.rows, rtol=0, atol=6e-10)
    dcb = tmp_path / "DCB_test_001_2026.dat"
    write_dcb_output(dcb, solution.combined_bias)
    satellites, receivers = _read_dcb(dcb)
    assert set(satellites) == set(expected_bias)
    assert set(receivers) == {name[0] for name in expected_bias}
    for constellation_name in receivers:
        group = [value for name, value in expected_bias.items() if name[0] == constellation_name]
        assert receivers[constellation_name] == pytest.approx(float(np.mean(group)), abs=5.1e-7)
        assert abs(sum(value for name, value in satellites.items() if name[0] == constellation_name)) <= 6e-6
    for name, expected in expected_bias.items():
        assert satellites[name] + receivers[name[0]] == pytest.approx(expected, abs=1.1e-6)
    assert solution.diagnostics["scientific_parity"] == "unverified"


@pytest.mark.parametrize(("latitude", "longitude"), [(0, 0), (52, 60), (76.5, -68.8), (45, 179.8), (88.7, 25)])
def test_angular_geometry_agrees_with_cartesian_intersection_including_low_elevations(latitude, longitude):
    elevation = np.repeat(np.asarray([0.0, 5.0, 10.0, 30.0, 60.0, 90.0]), 8)
    azimuth = np.tile(np.arange(0.0, 360.0, 45.0), 6)
    expected_lat, expected_lon, expected_mapping, _, _ = _cartesian_truth(elevation, azimuth, latitude, longitude, 0.94)
    actual_lat, actual_lon = pierce_point(elevation, azimuth, latitude, longitude)
    longitude_error = np.degrees(np.angle(np.exp(1j * np.radians(actual_lon - expected_lon))))
    np.testing.assert_allclose(actual_lat, expected_lat, rtol=0, atol=2e-10)
    np.testing.assert_allclose(longitude_error, 0, rtol=0, atol=2e-10)
    np.testing.assert_allclose(mapping_factor(elevation, correction=0.94), expected_mapping, rtol=0, atol=2e-13)


def test_full_day_at_one_minute_preserves_more_than_1999_samples_and_matches_coarse_grid(tmp_path):
    files, expected_bias, _ = _make_day(tmp_path / "input", cadence_seconds=30)
    data = [read_dat(path) for path in files]
    assert all(len(series.hour) == 2880 for series in data)
    fine_config = SolverConfig(model="published", time_step_hours=1 / 60)
    prepared = prepare_observations(data, fine_config)
    assert len(prepared.hour) == 2880 * len(data)
    fine = reconstruct(data, fine_config)
    coarse = reconstruct(data, SolverConfig(model="published"))
    assert fine.diagnostics["solved_epochs"] == len(fine.rows) == 1440
    np.testing.assert_allclose(fine.rows[:, 1:], _expected_coefficients(fine.rows[:, 0]), rtol=0, atol=3e-7)
    np.testing.assert_allclose(fine.rows[::30], coarse.rows, rtol=0, atol=3e-7)
    for name, expected in expected_bias.items():
        assert fine.combined_bias[name] == pytest.approx(expected, abs=3e-7)


def test_input_permutation_does_not_change_station_solution_or_constellation_gauge(tmp_path):
    files, _, _ = _make_day(tmp_path / "input")
    observations = [read_dat(path) for path in files]
    original = reconstruct(observations, SolverConfig(model="published"))
    order = np.random.default_rng(707).permutation(len(observations))
    permuted = reconstruct([observations[i] for i in order], SolverConfig(model="published"))
    np.testing.assert_allclose(permuted.rows, original.rows, rtol=0, atol=2e-9)
    for name, value in original.combined_bias.items():
        assert permuted.combined_bias[name] == pytest.approx(value, abs=2e-9)
    original_satellites, original_receivers = split_biases(original.combined_bias)
    permuted_satellites, permuted_receivers = split_biases(permuted.combined_bias)
    for name, value in original_satellites.items():
        assert permuted_satellites[name] == pytest.approx(value, abs=2e-9)
    for name, value in original_receivers.items():
        assert permuted_receivers[name] == pytest.approx(value, abs=2e-9)


def test_arbitrary_phase_ambiguities_cancel_without_changing_physical_coefficients(tmp_path):
    ordinary, _, _ = _make_day(tmp_path / "ordinary")
    shifted, expected_bias, _ = _make_day(tmp_path / "shifted", ambiguity_shift=12000.0)
    original = reconstruct([read_dat(path) for path in ordinary], SolverConfig(model="published"))
    changed = reconstruct([read_dat(path) for path in shifted], SolverConfig(model="published"))
    np.testing.assert_allclose(changed.rows, original.rows, rtol=0, atol=3e-7)
    for name, expected in expected_bias.items():
        assert changed.combined_bias[name] == pytest.approx(expected, abs=3e-7)


def test_link_bias_offsets_change_only_combined_bias_and_reporting_gauge(tmp_path):
    ordinary, old_bias, _ = _make_day(tmp_path / "ordinary")
    shifts = np.asarray([8 + j * 0.1 if j < 6 else -5 - j * 0.2 for j in range(12)])
    shifted, expected_bias, _ = _make_day(tmp_path / "shifted", link_shifts=shifts)
    original = reconstruct([read_dat(path) for path in ordinary], SolverConfig(model="published"))
    changed = reconstruct([read_dat(path) for path in shifted], SolverConfig(model="published"))
    np.testing.assert_allclose(changed.rows, original.rows, rtol=0, atol=2e-8)
    for name, expected in expected_bias.items():
        assert changed.combined_bias[name] - original.combined_bias[name] == pytest.approx(
            expected - old_bias[name], abs=2e-8
        )


@pytest.mark.parametrize("constellation", ["G", "R"])
def test_alternative_p1p2_signal_combination_retains_identical_physical_units(tmp_path, constellation):
    files, expected_bias, _ = _make_day(tmp_path / "input", constellation=constellation, code_combination="tec.p1p2")
    solution = reconstruct([read_dat(path) for path in files], SolverConfig(model="published"))
    np.testing.assert_allclose(solution.rows[:, 1:], _expected_coefficients(solution.rows[:, 0]), rtol=0, atol=2e-7)
    for name, expected in expected_bias.items():
        assert solution.combined_bias[name] == pytest.approx(expected, abs=2e-7)


def test_valid_zero_carrier_measurements_still_recover_the_full_physical_field(tmp_path):
    files, _, _ = _make_day(tmp_path / "input", zero_phase=True)
    observations = [read_dat(path) for path in files]
    assert all(data.phase_tec[0] == 0 and data.phase_valid[0] for data in observations)
    prepared = prepare_observations(observations, SolverConfig(model="published"))
    assert len(prepared.hour) == sum(len(data.hour) for data in observations)
    solution = reconstruct(observations, SolverConfig(model="published"))
    np.testing.assert_allclose(solution.rows[:, 1:], _expected_coefficients(solution.rows[:, 0]), rtol=0, atol=2e-7)


def test_missing_phase_measurements_split_leveling_arcs_and_do_not_bias_the_recovery(tmp_path):
    files, _, _ = _make_day(tmp_path / "input", missing_phase=True)
    observations = [read_dat(path) for path in files]
    solution = reconstruct(observations, SolverConfig(model="published"))
    assert solution.diagnostics["missing_phase"] == 4 * len(observations)
    assert solution.diagnostics["accepted_arcs"] == 4 * len(observations)
    assert solution.diagnostics["leveled_observations"] == sum(len(data.hour) for data in observations) - 4 * len(
        observations
    )
    np.testing.assert_allclose(solution.rows[:, 1:], _expected_coefficients(solution.rows[:, 0]), rtol=0, atol=2e-7)


def test_noisy_code_and_phase_remain_close_to_injected_physical_field(tmp_path):
    files, _, _ = _make_day(tmp_path / "input", code_noise=3.0, phase_noise=0.01)
    solution = reconstruct([read_dat(path) for path in files], SolverConfig(model="published"))
    expected = _expected_coefficients(solution.rows[:, 0])
    error = solution.rows[:, 1:] - expected
    assert np.all(np.isfinite(error))
    # A conservative, declared scenario threshold: 3 TECU pseudorange noise
    # and .01 TECU carrier noise should retain sub-TECU daily VTEC accuracy
    # after arc leveling. It is not a universal field-data error bound.
    assert np.sqrt(np.mean(error[:, 0] ** 2)) < 1.0
    assert np.max(np.abs(error[:, 0])) < 2.0
    assert np.sqrt(np.mean(error[:, 1] ** 2)) < 0.25
    assert np.sqrt(np.mean(error[:, 2] ** 2)) < 0.25


def test_true_missing_interval_stays_nan_through_output_roundtrip(tmp_path):
    files, _, _ = _make_day(tmp_path / "input", gap=(10.0, 14.0))
    solution = reconstruct([read_dat(path) for path in files], SolverConfig(model="published"))
    unavailable = (solution.rows[:, 0] >= 11) & (solution.rows[:, 0] <= 13)
    assert np.all(np.isnan(solution.rows[unavailable, 1:]))
    solved = np.isfinite(solution.rows[:, 1])
    assert np.any(solved & (solution.rows[:, 0] >= 14))
    np.testing.assert_allclose(
        solution.rows[solved, 1:], _expected_coefficients(solution.rows[solved, 0]), rtol=0, atol=3e-7
    )
    output = tmp_path / "with_gaps.dat"
    write_tec_output(output, solution.rows)
    np.testing.assert_allclose(read_tec_output(output), solution.rows, rtol=0, atol=6e-10, equal_nan=True)


def test_shared_satellite_timestamps_respect_strict_window_edges(tmp_path):
    files, _, _ = _make_day(tmp_path / "input", cadence_seconds=120)
    solution = reconstruct([read_dat(path) for path in files], SolverConfig(model="published"))
    # An interior open two-hour window contains59 two-minute times, each
    # shared by12 satellites. Including either boundary adds12 whole rows.
    assert solution.diagnostics["max_window_observations"] == 59 * 12


@pytest.mark.parametrize("geometry", ["constant", "nearly-constant"])
def test_unidentifiable_physical_sky_geometry_fails_explicitly(tmp_path, geometry):
    files, _, _ = _make_day(tmp_path / "input", geometry=geometry)
    with pytest.raises(SolverError, match=r"full-rank|ill-conditioned|rank-deficient|identifiable"):
        reconstruct([read_dat(path) for path in files], SolverConfig(model="published"))


def _single_satellite_dat(tmp_path, elevation, phase, code, cadence=60):
    """One satellite, one continuous arc, elevation-correlated code noise."""
    folder = tmp_path / "single"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "test_g01_001_2026.dat"
    header = (
        "# Satellite: G01\n# Site: test\n# Interval: 60\n"
        "# Position (L, B, H): 60.0, 52.0, 100\n"
        "# Columns: tsn, hour, el, az, tec.l1l2, tec.c1p2, validity\n"
    )
    rows = "".join(
        f"{i} {i * cadence / 3600:.12g} {e:.12g} 90 {p:.12g} {c:.12g} 0\n"
        for i, (e, p, c) in enumerate(zip(elevation, phase, code, strict=True))
    )
    path.write_text(header + rows, encoding="ascii")
    return path


def test_published_leveling_uses_1_over_s_weighted_mean(tmp_path):
    elevation = np.linspace(15.0, 85.0, 30)
    rng = np.random.default_rng(5)
    phase = 30.0 + 0.02 * np.arange(30)
    # Elevation-correlated code noise: weighted and plain means must differ.
    code = phase + 20.0 + 4.0 * (85.0 - elevation) / 70.0 + rng.normal(0, 0.05, 30)
    path = _single_satellite_dat(tmp_path, elevation, phase, code)
    prepared = prepare_observations([read_dat(path)], SolverConfig(model="published"))
    mapping = mapping_factor(elevation, correction=0.97)
    weighted = float(np.sum((code - phase) / mapping) / np.sum(1.0 / mapping))
    plain = float(np.mean(code - phase))
    assert weighted != pytest.approx(plain, abs=1e-9)
    assert prepared.diagnostics["leveling"] == "1/S elevation-weighted phase-to-code mean (TuRBOTEC stage 4)"
    assert len(prepared.hour) == 30
    np.testing.assert_allclose(prepared.slant_tec, phase + weighted, rtol=0, atol=1e-9)


def test_auto_mapping_alpha_follows_turbotec_latitude_bands(tmp_path):
    from absoltec_native.geometry import turbotec_alpha

    assert turbotec_alpha(1.3) == turbotec_alpha(-1.3) == 0.87
    assert turbotec_alpha(52.2) == 0.97
    assert turbotec_alpha(76.5) == turbotec_alpha(-76.5) == 0.94
    files, _, _ = _make_day(tmp_path / "mid", latitude=52.2)
    mid = reconstruct([read_dat(p) for p in files], SolverConfig(model="published", auto_mapping_alpha=True))
    assert mid.diagnostics["mapping_correction"] == 0.97
    low_files, _, _ = _make_day(tmp_path / "low", latitude=1.3)
    low = reconstruct([read_dat(p) for p in low_files], SolverConfig(model="published", auto_mapping_alpha=True))
    assert low.diagnostics["mapping_correction"] == 0.87
    with pytest.raises(ValueError, match="auto_mapping_alpha requires model='published'"):
        SolverConfig(model="legacy-2017", auto_mapping_alpha=True)


def _prepared_low_field(v0, satellites=8):
    """PreparedObservations with a flat, low vertical field V(t) = v0 + 0.1*sin.

    The time variation keeps the combined biases identifiable (a perfectly
    constant field would confound them with V, which the solver rejects).
    Every true V(t) lies in [v0-0.1, v0+0.1], below TuRBOTEC's C = 0.5 floor.
    """
    ids = tuple(f"G{i:02d}" for i in range(1, satellites + 1))
    hour_parts, slant_parts, map_parts, dlon_parts, dlat_parts, sv_parts = [], [], [], [], [], []
    for j in range(satellites):
        hour = np.linspace(0.0, 23.5, 96)
        # Elevation-like mapping variation along the track: a fixed mapping
        # per satellite would leave the biases nearly confounded with V.
        mapping = 1.2 + 0.05 * j + 0.6 * np.sin(2 * np.pi * (hour + 0.4 * j) / 24.0)
        bias = 3.0 + 0.5 * j
        field = v0 + 0.1 * np.sin(4 * np.pi * hour / 24.0)
        slant = mapping * field + bias
        # Each satellite sweeps across the sky, as real tracks do: fixed
        # pierce points would confound the biases with the spatial expansion.
        dlon = 2.0 * (j - (satellites - 1) / 2) / 10.0 + 1.5 * np.sin(2 * np.pi * hour / 24.0 + j)
        dlat = np.cos(j) * 1.5 + 1.5 * np.cos(2 * np.pi * hour / 24.0 + 0.7 * j)
        hour_parts.append(hour)
        slant_parts.append(slant)
        map_parts.append(mapping)
        dlon_parts.append(dlon)
        dlat_parts.append(dlat)
        sv_parts.append(np.full(96, j, dtype=np.int64))
    hour = np.concatenate(hour_parts)
    order = np.argsort(hour, kind="stable")
    return PreparedObservations(
        *(np.concatenate(part)[order] for part in (hour_parts, slant_parts, map_parts, dlon_parts, dlat_parts)),
        np.concatenate(sv_parts)[order],
        ids,
        {"input_files": satellites, "satellites": satellites},
    )


def test_nonneg_clamps_low_vertical_tec_and_preserves_slant_tec(tmp_path):
    prepared = _prepared_low_field(0.2)
    unconstrained = fit_prepared(prepared, SolverConfig(model="published"))
    bounded = fit_prepared(prepared, SolverConfig(model="published", nonneg=True))
    # The unconstrained vertical TEC is below TuRBOTEC's C = 0.5 floor ...
    assert np.nanmin(unconstrained.rows[:, 1]) < 0.5
    # ... and the bounded solve lifts it onto or above the bound, with the
    # lowest epochs pinned exactly at C (others rise to satisfy the DCB caps).
    assert np.all(bounded.rows[:, 1] >= 0.5 - 1e-8)
    assert np.min(bounded.rows[:, 1]) == pytest.approx(0.5, abs=1e-8)
    assert bounded.diagnostics["active_bounds"] >= 1
    # Slant TEC along every used line of sight stays above C = 0.5.
    for j, name in enumerate(prepared.satellite_ids):
        assert bounded.combined_bias[name] <= np.min(prepared.slant_tec[prepared.satellite == j]) - 0.5 + 1e-8


def test_nonneg_matches_unconstrained_solve_when_bounds_are_inactive(tmp_path):
    files, _, _ = _make_day(tmp_path / "input")
    data = [read_dat(path) for path in files]
    plain = reconstruct(data, SolverConfig(model="published"))
    bounded = reconstruct(data, SolverConfig(model="published", nonneg=True))
    assert bounded.diagnostics["solver"].startswith("bounded-variable least squares")
    np.testing.assert_allclose(bounded.rows, plain.rows, rtol=0, atol=1e-4, equal_nan=True)
    for name, value in plain.combined_bias.items():
        assert bounded.combined_bias[name] == pytest.approx(value, abs=1e-4)
