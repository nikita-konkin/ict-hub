"""MapTEC converter form, mounts and live progress checks."""

import pytest

from app import config as cfg
from app.converters import FormError, prepare_form
from app.registry import CONVERTERS, build_command
from app.runner import parse_progress


async def test_maptec_form_uses_configured_paths_and_progress(monkeypatch):
    monkeypatch.setattr(cfg, "TECSUITE_OUT_DAT_DATA_PATH_HOST", "/srv/dat")
    monkeypatch.setattr(cfg, "MAPTEC_NATIVE_OUTPUT_DATA_PATH_HOST", "/srv/map")
    form = {"year": "2024", "days": "365-366", "site": "abcd", "ut_start": "1.25", "dat_path": "/wrong"}
    prepared = await prepare_form("maptec-native", form, None)
    command, volumes = build_command("maptec-native", form)
    assert prepared.output_path == "/srv/map"
    assert volumes == {"/srv/dat": {"bind": "/data/in", "mode": "ro"}, "/srv/map": {"bind": "/data/out", "mode": "rw"}}
    assert command[command.index("--ut-start") + 1] == "1.25"
    patterns = CONVERTERS["maptec-native"]["progress_patterns"]
    assert parse_progress("Writing maps 2 / 4", patterns) == 50


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"days": "366"}, "day"),
        ({"site": "../bad"}, "Site"),
        ({"ut_start": "NaN"}, "finite"),
        ({"min_lat": "43", "max_lat": "42"}, "Latitude"),
    ],
)
async def test_maptec_form_rejects_invalid_settings(monkeypatch, overrides, message):
    monkeypatch.setattr(cfg, "TECSUITE_OUT_DAT_DATA_PATH_HOST", "/srv/dat")
    monkeypatch.setattr(cfg, "MAPTEC_NATIVE_OUTPUT_DATA_PATH_HOST", "/srv/map")
    with pytest.raises(FormError, match=message):
        await prepare_form("maptec-native", {"year": "2025", "days": "035", **overrides}, None)
