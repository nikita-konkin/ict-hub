"""Checks on Dockerfile.absoltec-native, the image the hub runs for AbsTEC Native jobs."""

from pathlib import Path


def test_native_image_runs_openblas_on_one_thread():
    text = (Path(__file__).resolve().parents[1] / "Dockerfile.absoltec-native").read_text(encoding="utf-8")
    assert "OPENBLAS_NUM_THREADS=1" in text, "native image does not pin OpenBLAS to one thread per station job"
