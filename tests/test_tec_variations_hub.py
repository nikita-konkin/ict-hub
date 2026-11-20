"""MapTEC converter form, mounts and live progress checks."""

import pytest

from app import config as cfg
from app.converters import FormError, prepare_form
from app.registry import CONVERTERS, build_command
from app.runner import parse_progress


async def test_tec_variations_form_uses_configured_paths_and_progress(monkeypatch):
    monkeypatch.setattr(cfg, "TECSUITE_OUT_DAT_DATA_PATH_HOST", "/srv/dat")
    monkeypatch.setattr(cfg, "TEC_VARIATIONS_OUTPUT_DATA_PATH_HOST", "/srv/map")
    form = {
        "year": "2024",
        "days": "365-366",
        "site": "abcd",
        "ut_start": "1.25",
        "dat_path": "/wrong",
        "bands": "5-15",
        "elevation_cutoff": "35",
        "png": "on",
    }
    prepared = await prepare_form("tec-variations", form, None)
    command, volumes = build_command("tec-variations", form)
    assert prepared.output_path == "/srv/map"
    assert volumes == {"/srv/dat": {"bind": "/data/in", "mode": "ro"}, "/srv/map": {"bind": "/data/out", "mode": "rw"}}
    assert command[command.index("--ut-start") + 1] == "1.25"
    assert command[command.index("--bands") + 1] == "5-15"
    assert command[command.index("--elevation-cutoff") + 1] == "35.0"
    assert "--png" in command
    assert "--legacy-maps" not in command
    assert CONVERTERS["tec-variations"]["label"] == "TEC Variations"
    patterns = CONVERTERS["tec-variations"]["progress_patterns"]
    assert parse_progress("Writing maps 2 / 4", patterns) == 50


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"days": "366"}, "day"),
        ({"site": "../bad"}, "Site"),
        ({"ut_start": "NaN"}, "finite"),
        ({"min_lat": "43", "max_lat": "42"}, "Latitude"),
        ({"bands": "2-10,02-10.0"}, "unique"),
        ({"bands": "10-2"}, "increasing"),
        ({"filter": "mean"}, "butterworth or legacy"),
        ({"elevation_cutoff": "95"}, "Elevation cutoff"),
    ],
)
async def test_tec_variations_form_rejects_invalid_settings(monkeypatch, overrides, message):
    monkeypatch.setattr(cfg, "TECSUITE_OUT_DAT_DATA_PATH_HOST", "/srv/dat")
    monkeypatch.setattr(cfg, "TEC_VARIATIONS_OUTPUT_DATA_PATH_HOST", "/srv/map")
    with pytest.raises(FormError, match=message):
        await prepare_form("tec-variations", {"year": "2025", "days": "035", **overrides}, None)
