"""Per-converter form preparation for the native AbsolTEC reconstruction."""

import pytest

from app import config as cfg
from app.converters import FormError, _abstec_native_page, _prepare_abstec_native


def _form(**overrides):
    form = {
        "year": "2025",
        "day_of_year": "032",
        "model": "published",
        "time_step_hours": "0.5",
        "elevation_cutoff": "10",
        "correction_coefficient": "0.97",
        "window_hours": "1.0",
        "jobs": "1",
    }
    form.update(overrides)
    return form


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(cfg, "TECSUITE_OUT_DAT_DATA_PATH_HOST", r"N:\DAT")
    monkeypatch.setattr(cfg, "ABSTEC_NATIVE_OUTPUT_DATA_PATH_HOST", r"N:\OUT")


async def test_native_form_fills_server_paths_and_normalizes_numbers(configured):
    form = _form(auto_alpha=True, nonneg=True)
    prepared = await _prepare_abstec_native(form, None)
    assert form["dat_path"] == r"N:\DAT"
    assert form["output_dir"] == r"N:\OUT"
    assert prepared.output_path == r"N:\OUT"
    assert form["elevation_cutoff"] == 10
    assert form["jobs"] == 1
    assert form["model"] == "published"


async def test_native_form_batch_days_are_accepted(configured):
    form = _form(day_of_year="", days="032-036")
    await _prepare_abstec_native(form, None)
    assert form["days"] == "032-036"


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        ({"year": ""}, "Year"),
        ({"year": "20x5"}, "Year"),
        ({"day_of_year": "", "days": ""}, "Select at least one day"),
        ({"day_of_year": "032", "days": "032-033"}, "not both"),
        ({"day_of_year": "0"}, "Day of year"),
        ({"day_of_year": "", "days": "032;"}, "Days must be"),
        ({"model": "legacy-2017", "auto_alpha": True}, "requires the published model"),
        ({"model": "legacy-2017", "nonneg": True}, "requires the published model"),
        ({"model": "legacy-2017", "shell_height_km": "506.7"}, "requires the published model"),
        ({"model": "something-else"}, "Model must be"),
        ({"elevation_cutoff": "90"}, "Elevation cutoff"),
        ({"jobs": "9"}, "Parallel jobs"),
        ({"correction_coefficient": "1.5"}, "at most 1"),
        ({"window_hours": "7"}, "at most 6"),
        ({"window_hours": "0"}, "positive"),
        ({"shell_height_km": "-5"}, "Shell height"),
    ],
)
async def test_native_form_rejections(configured, bad, message):
    with pytest.raises(FormError, match=message):
        await _prepare_abstec_native(_form(**bad), None)


async def test_native_form_requires_configuration(monkeypatch):
    monkeypatch.setattr(cfg, "TECSUITE_OUT_DAT_DATA_PATH_HOST", "")
    monkeypatch.setattr(cfg, "ABSTEC_NATIVE_OUTPUT_DATA_PATH_HOST", r"N:\OUT")
    with pytest.raises(FormError, match="TECSUITE_OUT_DAT_DATA_PATH_HOST"):
        await _prepare_abstec_native(_form(), None)

    monkeypatch.setattr(cfg, "TECSUITE_OUT_DAT_DATA_PATH_HOST", r"N:\DAT")
    monkeypatch.setattr(cfg, "ABSTEC_NATIVE_OUTPUT_DATA_PATH_HOST", "")
    with pytest.raises(FormError, match="ABSTEC_NATIVE_OUTPUT_DATA_PATH_HOST"):
        await _prepare_abstec_native(_form(), None)


async def test_native_page_context_reports_native_output_path(monkeypatch):
    monkeypatch.setattr(cfg, "TECSUITE_OUT_DAT_DATA_PATH_CONTAINER", "/mnt/tecsuite-out")
    monkeypatch.setattr(cfg, "TECSUITE_OUT_DAT_DATA_PATH_HOST", "")
    monkeypatch.setattr(cfg, "ABSTEC_NATIVE_OUTPUT_DATA_PATH_HOST", r"N:\OUT")
    async def fake_tree(scan_path):
        return [{"year": "2025", "days": [{"day": "032"}]}] if scan_path else []

    monkeypatch.setattr("app.converters.list_tecsuite_output_structure_async", fake_tree)
    context = await _abstec_native_page()
    assert context["abstec_output_host_path"] == r"N:\OUT"
    assert context["abstec_dat_tree"][0]["year"] == "2025"
