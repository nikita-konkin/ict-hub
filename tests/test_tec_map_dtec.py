"""Exercise IonMaps with real converter-format dTEC station tables."""

import time
from datetime import UTC, date, datetime
from io import BytesIO

import numpy as np
import pyarrow as pa
import pytest
from PIL import Image

from app import config, tec_map_jobs
from tec_variations.parquet import FilteredArc, observation_batch, observation_schema, write_station_day
from tec_variations.settings import VariationSettings

SNAPSHOT = "/tec-map/dtec/snapshot"
JOBS = "/tec-map/dtec/gif/jobs"
AVAILABILITY = "/tec-map/dtec/availability"
BAND = "dtec_10_20m"
SNAPSHOT_PARAMS = {"stations": "tver", "timestamp": "2024-12-31T23:59:00Z", "band": BAND}
RANGE_PARAMS = {
    "stations": "tver",
    "start_time": "2024-12-31T23:59:00Z",
    "end_time": "2025-01-01T00:02:00Z",
    "band": BAND,
}


@pytest.fixture
def dtec_root(tmp_path, monkeypatch):
    def save(day, station, tracks, *, fallback=False):
        settings = VariationSettings(bands="2.5-8,10-20", shell_height_km=340)
        bands = settings.period_bands
        schema = observation_schema(settings, bands)
        batches = []
        for satellite, seconds, values in tracks:
            count = len(seconds)
            arc = FilteredArc(
                samples=np.column_stack([seconds, np.full(count, 42), np.zeros((count, 3))]),
                latitude=np.full(count, 56),
                longitude=np.full(count, 36),
                factor=np.ones(count),
                variations={bands[0].column: np.full(count, 9), BAND: np.asarray(values, dtype=float)},
                smoothed_tec=np.zeros(count),
            )
            batches.append(observation_batch(day, station, satellite, 0, arc, schema))
        table = pa.Table.from_batches(batches, schema)
        if fallback:
            table = table.replace_schema_metadata({b"shell_height_km": b"340"})
        day_dir = tmp_path / f"{day.year}" / f"{day.timetuple().tm_yday:03d}"
        day_dir.mkdir(parents=True, exist_ok=True)
        write_station_day(day_dir, day, station, table)

    save(
        date(2024, 12, 31),
        "tver",
        [("G01", [86210, 86330, 86350, 86390], [90, -2, 3, 4]), ("G02", [86345], [1])],
    )
    save(date(2024, 12, 31), "ulyn", [("G01", [86340], [80])])
    save(date(2025, 1, 1), "tver", [("G01", [5, 10], [-6, 7])], fallback=True)
    monkeypatch.setattr(config, "TEC_VARIATIONS_OUTPUT_DATA_PATH_CONTAINER", str(tmp_path), raising=False)
    return tmp_path


def test_reading_station_time_band_across_midnight(dtec_root):
    from app.tec_map_dtec import read_observations, select_files

    start = datetime(2024, 12, 31, 23, 59, tzinfo=UTC)
    end = datetime(2025, 1, 1, 0, 0, 5, tzinfo=UTC)
    files = select_files(dtec_root, ("tver",), start, end, "dtec_2p5_8m")
    observations = read_observations(files)
    assert list(observations["station"].unique()) == ["tver"]
    assert observations["dtec"].tolist() == [9, 9, 9, 9]
    assert "azimuth_deg" not in observations
    assert observations["datetime_utc"].min().to_pydatetime() == datetime(2024, 12, 31, 23, 59, 5, tzinfo=UTC)
    assert observations["datetime_utc"].max().to_pydatetime() == end


def test_frames_use_nearest_sample_per_link_and_keep_empty_frames(dtec_root):
    from app.tec_map_dtec import build_frames, read_observations, select_files

    start = datetime(2024, 12, 31, 23, 59, tzinfo=UTC)
    end = datetime(2025, 1, 1, 0, 1, tzinfo=UTC)
    files = select_files(dtec_root, ("tver",), start.replace(second=0), end, BAND)
    frames = build_frames(read_observations(files), start, 60, 3)
    assert list(frames) == [start, end.replace(minute=0), end]
    assert frames[start].set_index("satellite")["dtec"].to_dict() == {"G01": 3, "G02": 1}
    assert frames[end.replace(minute=0)]["dtec"].tolist() == [-6]
    assert frames[end].empty


def test_auto_limit_is_symmetric_with_floor_and_fixed_override():
    from app.tec_map_dtec import color_limits

    assert color_limits(np.array([-4.0, 2.0, 1.0]), None) == pytest.approx((-3.92, 3.92))
    assert color_limits(np.array([0.0]), None) == (-0.01, 0.01)
    assert color_limits(np.array([-4.0]), 0.5) == (-0.5, 0.5)


@pytest.mark.parametrize(
    ("params", "detail"),
    [
        ({"band": "dtec_45_55m"}, "Unknown dTEC band"),
        ({"stations": "../tver"}, "Invalid station name"),
        ({"step_seconds": "abc"}, "Frame step must be 30, 60, 120 or 300 seconds"),
    ],
)
def test_bad_queries_return_400(admin_client, dtec_root, params, detail):
    response = admin_client.get(SNAPSHOT, params=SNAPSHOT_PARAMS | params)
    assert response.status_code == 400
    assert response.json()["detail"] == detail


def test_missing_root_returns_404(admin_client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "TEC_VARIATIONS_OUTPUT_DATA_PATH_CONTAINER", str(tmp_path / "missing"), raising=False)
    response = admin_client.get(AVAILABILITY)
    assert response.status_code == 404
    assert response.json()["detail"] == "TEC Variations output is not configured"


def test_missing_time_rows_return_404_before_queuing(admin_client, dtec_root):
    response = admin_client.post(
        JOBS, params=RANGE_PARAMS | {"start_time": "2024-12-31T22:00:00Z", "end_time": "2024-12-31T22:02:00Z"}
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "No dTEC samples found for the requested stations and time"


def test_too_many_frames_return_413(admin_client, dtec_root):
    response = admin_client.post(JOBS, params=RANGE_PARAMS | {"end_time": "2025-01-02T00:00:00Z"})
    assert response.status_code == 413
    assert response.json()["detail"] == "Request exceeds the limit of 1440 frames"


def test_job_key_has_a_dtec_namespace(admin_client, dtec_root, monkeypatch):
    calls = []

    def submit(user_id, key, work):
        calls.append(key)
        return tec_map_jobs.AnimationJob("example", user_id, key, work)

    monkeypatch.setattr(tec_map_jobs, "submit", submit)
    response = admin_client.post(JOBS, params=RANGE_PARAMS)
    assert response.status_code == 202
    assert calls[0].startswith("dtec:")
    assert response.json()["status_url"] == "/tec-map/gif/jobs/example"


def test_snapshot_exposes_ipp_values_and_metadata(admin_client, dtec_root):
    response = admin_client.get(SNAPSHOT, params=SNAPSHOT_PARAMS | {"terminator": "true"})
    assert response.status_code == 200
    payload = response.json()
    point_trace = payload["data"][0]
    assert point_trace["marker"]["color"] == [-2, 1]
    assert point_trace["marker"]["cmin"] == -point_trace["marker"]["cmax"]
    assert point_trace["customdata"] == [["tver", "G01", 42], ["tver", "G02", 42]]
    assert "2 points" in payload["layout"]["title"]["text"]
    assert "340 km" in str(payload["layout"])
    assert payload["data"][1]["mode"] == "lines"


def test_background_job_renders_real_gif_with_empty_frame(admin_client, dtec_root):
    started = admin_client.post(JOBS, params=RANGE_PARAMS | {"frame_dpi": 50, "terminator": "true"})
    assert started.status_code == 202
    job = started.json()
    deadline = time.monotonic() + 10
    while job["status"] in {"queued", "running"} and time.monotonic() < deadline:
        time.sleep(0.05)
        job = admin_client.get(job["status_url"]).json()
    assert job["status"] == "done", job
    result = admin_client.get(job["result_url"])
    assert result.headers["content-type"] == "image/gif"
    with Image.open(BytesIO(result.content)) as image:
        assert image.n_frames == 3


def test_availability_lists_converter_stations_and_bands(admin_client, dtec_root):
    tree = admin_client.get(AVAILABILITY).json()
    assert tree == {"years": [2024, 2025], "doysByYear": {"2024": [366], "2025": [1]}}
    for year, doy, stations in [(2024, 366, ["tver", "ulyn"]), (2025, 1, ["tver"])]:
        response = admin_client.get(AVAILABILITY, params={"year": year, "doy": doy})
        assert response.status_code == 200
        assert response.json() == {
            "stations": stations,
            "bands": [
                {"column": "dtec_2p5_8m", "minutes": [2.5, 8]},
                {"column": BAND, "minutes": [10, 20]},
            ],
        }


def test_dtec_requires_authentication(client):
    assert client.get(AVAILABILITY).status_code == 401


def test_ionmaps_page_exposes_data_selector_and_dtec_controls(admin_client):
    response = admin_client.get("/ionmaps?lang=en")
    assert response.status_code == 200
    for text in (
        'id="tecmap-data"',
        "VTEC (TEC-suite)",
        "TEC variations (dTEC)",
        'id="tecmap-dtec-band"',
        'id="tecmap-dtec-limit"',
        'id="tecmap-dtec-step"',
        'id="tecmap-dtec-terminator"',
    ):
        assert text in response.text
