import numpy as np
import pytest

from absoltec_native.geometry import longitude_offset, mapping_factor, pierce_point


def test_zenith_maps_to_station_and_has_unit_mapping():
    lat, lon = pierce_point([90, 90], [0, 180], 55.76, 60.71)
    np.testing.assert_allclose(lat, 55.76)
    np.testing.assert_allclose(lon, 60.71)
    np.testing.assert_allclose(mapping_factor([90, 90]), 1)


def test_cardinal_symmetry_at_equator():
    lat, lon = pierce_point([30] * 4, [0, 90, 180, 270], 0, 0)
    assert lat[0] > 0
    assert lat[2] < 0
    assert lon[1] > 0
    assert lon[3] < 0
    np.testing.assert_allclose(lat[0], -lat[2])
    np.testing.assert_allclose(lon[1], -lon[3])
    np.testing.assert_allclose(lat[[1, 3]], 0, atol=1e-14)
    np.testing.assert_allclose(lon[[0, 2]], 0, atol=1e-14)


def test_polar_crossing_and_dateline_are_finite():
    lat, lon = pierce_point(10, 0, 89, 179)
    assert np.isfinite(lat)
    assert np.isfinite(lon)
    assert abs(float(longitude_offset(lon, 179))) > 170
    np.testing.assert_allclose(longitude_offset([-179, 179], 180), [1, -1])


def test_correction_is_applied_to_angle_not_tec():
    expected = 1 / np.sqrt(1 - (6371 / 6821 * np.sin(np.deg2rad(0.97 * 80))) ** 2)
    np.testing.assert_allclose(mapping_factor(10), expected)
    assert mapping_factor(10, correction=1) > mapping_factor(10)


@pytest.mark.parametrize("value", [-1, 91, np.nan, np.inf])
def test_bad_elevations_rejected(value):
    with pytest.raises(ValueError, match="Elevation"):
        mapping_factor(value)
    with pytest.raises(ValueError, match="Elevation"):
        pierce_point(value, 0, 0, 0)
