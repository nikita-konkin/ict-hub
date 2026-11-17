import numpy as np
import pytest

from absoltec_native.bias import split_biases, write_dcb_output


def test_gauge_preserves_combined_bias_and_each_constellation_zero_mean():
    combined = {"G01": -30.0, "G02": -40.0, "R01": 15.0, "R24": 21.0}
    satellites, receivers = split_biases(combined)
    assert receivers == {"G": -35.0, "R": 18.0}
    for sv, value in combined.items():
        assert satellites[sv] + receivers[sv[0]] == value
    for constellation in "GR":
        np.testing.assert_allclose(sum(v for k, v in satellites.items() if k.startswith(constellation)), 0)


def test_missing_constellation_is_not_reported_as_nan(tmp_path):
    path = tmp_path / "DCB_test_001_2025.dat"
    write_dcb_output(path, {"G01": 20, "G02": 22})
    assert path.read_text().splitlines() == [
        "# DCB sat:",
        "G 1   -1.000000",
        "G 2    1.000000",
        "# DCB rec:",
        "G   21.000000",
    ]
    with pytest.raises(FileExistsError):
        write_dcb_output(path, {"G01": 1})


@pytest.mark.parametrize("bad", [{}, {"E01": 1}, {"G01": float("nan")}])
def test_bad_biases_are_rejected(bad):
    with pytest.raises(ValueError, match=r"No fitted biases|GPS/GLONASS"):
        split_biases(bad)
