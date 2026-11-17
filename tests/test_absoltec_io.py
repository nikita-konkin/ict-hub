"""Independent format fixtures for native AbsolTEC input and output."""

import numpy as np
import pytest

from absoltec_native.io import DatFormatError, read_dat, read_tec_output, write_tec_output


def make_input(tmp_path, rows, *, satellite="G01", code="tec.c1p2", columns=None):
    path = tmp_path / f"test_{satellite}_001_26.dat"
    column_names = columns or f"tsn, hour, el, az, tec.l1l2, {code}, validity"
    path.write_text(
        f"# Satellite: {satellite}" + (" (k = -7)" if satellite.startswith("R") else "") + "\n"
        "# Interval: 30\n# Sampling interval: 0.0 (not used).\n# Site: test\n"
        "# Position (L, B, H): 359.8, 55.1, 120.3\n"
        f"# Columns: {column_names}\n# (I11,F14.11,F10.5,F11.5,F21.3,F10.3,I7)\n" + rows,
        encoding="utf-8",
    )
    return path


def test_validity_is_missing_bitmask_and_zero_tec_is_retained(tmp_path):
    path = make_input(tmp_path, "0 0 25 0 0 0 0\n1 0.008333333333 26 1 1 0 68\n2 0.01666666667 27 2 0 2 24\n")
    data = read_dat(path)
    assert data.phase_tec.tolist() == [0, 1, 0]
    assert data.phase_valid.tolist() == [True, True, False]
    assert data.code_valid.tolist() == [True, False, True]
    assert data.code_tec.tolist() == [0, 0, 2]
    assert data.diagnostics.missing_phase_rows == data.diagnostics.missing_code_rows == 1


def test_glonass_metadata_and_cycle_slip_preserved(tmp_path):
    path = make_input(tmp_path, "0 0 25 0 10 20 0\n1 0.008333333333 26 1 11 21 1\n", satellite="R01")
    data = read_dat(path)
    assert data.satellite_id == "R01"
    assert data.glonass_channel == -7
    assert data.longitude_deg == 359.8
    assert data.latitude_deg == 55.1
    assert data.height_m == 120.3
    assert data.year is None  # The filename's two-digit year has no century.
    assert data.break_before.tolist() == [True, True]
    assert data.phase_valid.tolist() == [True, True]
    assert data.diagnostics.cycle_slip_rows == 1


def test_legacy_glonass_header_uses_integer_valued_decimal_channel(tmp_path):
    path = make_input(tmp_path, "0 0 25 0 10 20 0\n", satellite="R01")
    path.write_text(path.read_text().replace("k = -7)", "k = -7.0)"))
    assert read_dat(path).glonass_channel == -7


def test_p1p2_missing_mask_differs_from_c1p2(tmp_path):
    path = make_input(tmp_path, "0 0 25 0 10 20 4\n1 0.008333333333 26 1 11 21 32\n", code="tec.p1p2")
    assert read_dat(path).code_valid.tolist() == [True, False]


def test_time_gaps_and_missing_phase_break_both_sides(tmp_path):
    path = make_input(
        tmp_path,
        "0 0 25 0 10 20 0\n1 0.008333333333 26 1 0 21 8\n2 0.01666666667 27 2 12 22 0\n4 0.03333333333 28 3 13 23 0\n",
    )
    data = read_dat(path)
    assert data.break_before.tolist() == [True, True, True, True]
    assert data.diagnostics.gaps == 1


@pytest.mark.parametrize(
    "row", ["0 0 20 10 nan 20 0", "0 0 20 10 10 20", "0 24 20 10 10 20 0", "0 0 91 10 10 20 0", "0 0 20 10 10 20 0.5"]
)
def test_bad_rows_raise_with_filename_and_line(tmp_path, row):
    path = make_input(tmp_path, row + "\n")
    with pytest.raises(DatFormatError, match=r"test_G01_001_26\.dat:8:"):
        read_dat(path)


def test_explicit_skip_counts_bad_rows_and_preserves_arc_boundary(tmp_path):
    path = make_input(
        tmp_path,
        "0 0 20 10 10 20 0\n1 0.008333333333 20 10 11 21 0\n1 0.008333333333 20 10 11 21 0\n2 0.01666666667 20 10 12 22 0\n",
    )
    data = read_dat(path, invalid_rows="skip")
    assert data.diagnostics.data_rows == 4
    assert data.diagnostics.kept_rows == 3
    assert data.diagnostics.skipped_rows == 1
    assert len(data.diagnostics.issues) == 1
    assert data.break_before.tolist() == [True, False, True]


def test_reordered_columns_and_fortran_exponent(tmp_path):
    path = make_input(tmp_path, "0 0 4.0D1 2.5D1 20 0 10\n", columns="tsn, hour, az, el, tec.p1p2, validity, tec.l1l2")
    data = read_dat(path)
    assert data.elevation[0] == 25
    assert data.azimuth[0] == 40
    assert data.phase_tec[0] == 10
    assert data.code_tec[0] == 20


def test_identity_conflict_fails(tmp_path):
    path = make_input(tmp_path, "0 0 20 10 10 20 0\n")
    path.write_text(path.read_text().replace("# Site: test", "# Site: oops"))
    with pytest.raises(DatFormatError, match="site header disagrees"):
        read_dat(path)


def test_tec_output_preserves_zero_and_nan_and_refuses_overwrite(tmp_path):
    destination = tmp_path / "test_001_2026.dat"
    rows = np.asarray([[0, 0, 1, 2, 3, 4, 5, 6], [0.5, *([np.nan] * 7)]])
    write_tec_output(destination, rows)
    np.testing.assert_allclose(read_tec_output(destination), rows, equal_nan=True)
    assert destination.read_text().startswith("# UT  I_v  G_lon  G_lat  G_q_lon  G_q_lat  G_t  G_q_t\n")
    before = destination.read_bytes()
    with pytest.raises(FileExistsError):
        write_tec_output(destination, rows * 2)
    assert destination.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))
    write_tec_output(destination, rows * 2, overwrite=True)
    np.testing.assert_allclose(read_tec_output(destination), rows * 2, equal_nan=True)


def test_invalid_output_does_not_replace_existing_data(tmp_path):
    destination = tmp_path / "result.dat"
    destination.write_text("original")
    with pytest.raises(ValueError, match="UT must increase"):
        write_tec_output(destination, np.zeros((2, 8)), overwrite=True)
    assert destination.read_text() == "original"
