"""Tests for TEC map GIF endpoints and rendering helpers."""

from __future__ import annotations

from io import BytesIO

import numpy as np
import pandas as pd
from PIL import Image

from app.main import app
import app.tec_map_render as tec_map_render_module
from app.tec_map_pipeline import TecMapConfig
from app.tec_map_render import TecMapRenderConfig, build_animation_gif_bytes, build_snapshot_plotly_json


def test_tec_map_gif_route_accepts_range_query_shape(client, monkeypatch):
    import app.tec_map as tec_map_module

    class _User:
        is_admin = True

        def can_access_page(self, name: str) -> bool:
            return name == "analysis"

    load_calls: list[dict[str, object]] = []

    def fake_load_tecs_parquet(**kwargs):
        load_calls.append(dict(kwargs))
        return pd.DataFrame({"placeholder": [1]})

    def fake_build_leveled_links(raw_links, config):
        return raw_links

    def fake_build_frame_summary(leveled_links, config):
        return pd.DataFrame(
            [
                {
                    "frame_time": pd.Timestamp("2026-01-02 00:00:00"),
                    "station": "aksu",
                    "site_lat": 55.8,
                    "site_lon": 49.1,
                    "ipp_lat": 55.9,
                    "ipp_lon": 49.2,
                    "vtec_tecu": 10.0,
                    "samples": 4,
                }
            ]
        )

    monkeypatch.setattr(tec_map_module.cfg, "PARQUET_OUTPUT_TECSUITE_DATA_PATH_CONTAINER", "/mnt/fake")
    monkeypatch.setattr(tec_map_module.cfg, "PARQUET_OUTPUT_TECSUITE_DATA_PATH_HOST", "")
    monkeypatch.setattr(tec_map_module, "load_tecs_parquet", fake_load_tecs_parquet)
    monkeypatch.setattr(tec_map_module, "build_leveled_links", fake_build_leveled_links)
    monkeypatch.setattr(tec_map_module, "build_frame_summary", fake_build_frame_summary)
    monkeypatch.setattr(tec_map_module, "build_animation_gif_bytes", lambda **kwargs: b"GIF89a-test")

    app.dependency_overrides[tec_map_module.get_current_user_or_401] = lambda: _User()
    try:
        response = client.get(
            "/tec-map/gif",
            params=[
                ("date", "2026-01-02"),
                ("stations", "aksu"),
                ("stations", "alme"),
                ("stations", "arsk"),
                ("stations", "kukm"),
                ("min_elevation_deg", "20"),
                ("sampling_interval_seconds", "300"),
                ("frame_minutes", "15"),
                ("ionosphere_height_km", "350"),
                ("grid_resolution_deg", "1"),
                ("smoothing_sigma", "1"),
                ("start_time", "00:00:00"),
                ("end_time", "23:00:00"),
                ("basemap", "false"),
            ],
        )
    finally:
        app.dependency_overrides.pop(tec_map_module.get_current_user_or_401, None)

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/gif"
    assert response.content == b"GIF89a-test"
    assert len(load_calls) == 1
    assert load_calls[0]["year"] == 2026
    assert load_calls[0]["doy"] == 2
    assert load_calls[0]["stations"] == ["aksu", "alme", "arsk", "kukm"]
    assert str(load_calls[0]["start_time"]).startswith("2026-01-02 00:00:00")
    assert str(load_calls[0]["end_time"]).startswith("2026-01-02 23:00:00")
    assert load_calls[0]["min_elevation_deg"] == 20.0


def test_tec_map_gif_route_supports_multi_day_range(client, monkeypatch):
    import app.tec_map as tec_map_module

    class _User:
        is_admin = True

        def can_access_page(self, name: str) -> bool:
            return name == "analysis"

    load_calls: list[dict[str, object]] = []

    def fake_load_tecs_parquet(**kwargs):
        load_calls.append(dict(kwargs))
        return pd.DataFrame({"placeholder": [1]})

    def fake_build_leveled_links(raw_links, config):
        return raw_links

    def fake_build_frame_summary(leveled_links, config):
        return pd.DataFrame(
            [
                {
                    "frame_time": pd.Timestamp("2026-01-02 00:00:00"),
                    "station": "aksu",
                    "site_lat": 55.8,
                    "site_lon": 49.1,
                    "ipp_lat": 55.9,
                    "ipp_lon": 49.2,
                    "vtec_tecu": 10.0,
                    "samples": 4,
                }
            ]
        )

    monkeypatch.setattr(tec_map_module.cfg, "PARQUET_OUTPUT_TECSUITE_DATA_PATH_CONTAINER", "/mnt/fake")
    monkeypatch.setattr(tec_map_module.cfg, "PARQUET_OUTPUT_TECSUITE_DATA_PATH_HOST", "")
    monkeypatch.setattr(tec_map_module, "load_tecs_parquet", fake_load_tecs_parquet)
    monkeypatch.setattr(tec_map_module, "build_leveled_links", fake_build_leveled_links)
    monkeypatch.setattr(tec_map_module, "build_frame_summary", fake_build_frame_summary)
    monkeypatch.setattr(tec_map_module, "build_animation_gif_bytes", lambda **kwargs: b"GIF89a-test")

    app.dependency_overrides[tec_map_module.get_current_user_or_401] = lambda: _User()
    try:
        response = client.get(
            "/tec-map/gif",
            params=[
                ("date", "2026-01-02"),
                ("end_date", "2026-01-03"),
                ("stations", "aksu"),
                ("stations", "alme"),
                ("min_elevation_deg", "20"),
                ("sampling_interval_seconds", "300"),
                ("frame_minutes", "120"),
                ("ionosphere_height_km", "350"),
                ("grid_resolution_deg", "1"),
                ("smoothing_sigma", "1"),
                ("start_time", "00:00:00"),
                ("end_time", "23:00:00"),
                ("basemap", "false"),
            ],
        )
    finally:
        app.dependency_overrides.pop(tec_map_module.get_current_user_or_401, None)

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/gif"
    assert response.content == b"GIF89a-test"
    assert len(load_calls) == 2
    assert [(call["year"], call["doy"]) for call in load_calls] == [(2026, 2), (2026, 3)]
    assert str(load_calls[0]["start_time"]).startswith("2026-01-02 00:00:00")
    assert str(load_calls[0]["end_time"]).startswith("2026-01-02 23:59:59")
    assert str(load_calls[1]["start_time"]).startswith("2026-01-03 00:00:00")
    assert str(load_calls[1]["end_time"]).startswith("2026-01-03 23:00:00")


def test_tec_map_gif_route_accepts_cache_only_basemap_mode(client, monkeypatch):
    import app.tec_map as tec_map_module

    class _User:
        is_admin = True

        def can_access_page(self, name: str) -> bool:
            return name == "analysis"

    captured_render: list[TecMapRenderConfig] = []

    def fake_build_frame_summary(_leveled_links, _config):
        return pd.DataFrame(
            [
                {
                    "frame_time": pd.Timestamp("2026-01-02 00:00:00"),
                    "station": "aksu",
                    "site_lat": 55.8,
                    "site_lon": 49.1,
                    "ipp_lat": 55.9,
                    "ipp_lon": 49.2,
                    "vtec_tecu": 10.0,
                    "samples": 4,
                }
            ]
        )

    def fake_build_animation_gif_bytes(**kwargs):
        captured_render.append(kwargs["render"])
        return b"GIF89a-test"

    monkeypatch.setattr(tec_map_module.cfg, "PARQUET_OUTPUT_TECSUITE_DATA_PATH_CONTAINER", "/mnt/fake")
    monkeypatch.setattr(tec_map_module.cfg, "PARQUET_OUTPUT_TECSUITE_DATA_PATH_HOST", "")
    monkeypatch.setattr(tec_map_module.cfg, "TEC_MAP_BASEMAP_CACHE_ROOT", "/mnt/cache")
    monkeypatch.setattr(tec_map_module.cfg, "TEC_MAP_BASEMAP_TILE_SERVER_URL", "http://tiles.test/{z}/{x}/{y}.png")
    monkeypatch.setattr(tec_map_module, "load_tecs_parquet", lambda **kwargs: pd.DataFrame({"placeholder": [1]}))
    monkeypatch.setattr(tec_map_module, "build_leveled_links", lambda raw_links, config: raw_links)
    monkeypatch.setattr(tec_map_module, "build_frame_summary", fake_build_frame_summary)
    monkeypatch.setattr(tec_map_module, "build_animation_gif_bytes", fake_build_animation_gif_bytes)

    app.dependency_overrides[tec_map_module.get_current_user_or_401] = lambda: _User()
    try:
        response = client.get(
            "/tec-map/gif",
            params=[
                ("date", "2026-01-02"),
                ("stations", "aksu"),
                ("start_time", "00:00:00"),
                ("end_time", "01:00:00"),
                ("basemap", "cache_only"),
            ],
        )
    finally:
        app.dependency_overrides.pop(tec_map_module.get_current_user_or_401, None)

    assert response.status_code == 200
    assert captured_render
    render = captured_render[0]
    assert render.basemap_enabled is True
    assert render.basemap_mode == "cache_only"
    assert render.basemap_cache_root is not None
    assert render.basemap_cache_root.parts[-2:] == ("mnt", "cache")
    assert render.basemap_tile_server_url == "http://tiles.test/{z}/{x}/{y}.png"


def test_build_animation_gif_bytes_returns_gif_bytes():
    frame_summary = pd.DataFrame(
        [
            {
                "frame_time": "2026-01-02 00:00:00",
                "station": "aksu",
                "site_lat": 55.8,
                "site_lon": 49.1,
                "ipp_lat": 55.9,
                "ipp_lon": 49.2,
                "vtec_tecu": 10.0,
                "samples": 4,
            },
            {
                "frame_time": "2026-01-02 00:00:00",
                "station": "alme",
                "site_lat": 54.9,
                "site_lon": 52.3,
                "ipp_lat": 55.0,
                "ipp_lon": 52.4,
                "vtec_tecu": 12.0,
                "samples": 4,
            },
            {
                "frame_time": "2026-01-02 00:00:00",
                "station": "arsk",
                "site_lat": 56.1,
                "site_lon": 49.9,
                "ipp_lat": 56.2,
                "ipp_lon": 50.0,
                "vtec_tecu": 11.0,
                "samples": 4,
            },
            {
                "frame_time": "2026-01-02 00:15:00",
                "station": "aksu",
                "site_lat": 55.8,
                "site_lon": 49.1,
                "ipp_lat": 56.0,
                "ipp_lon": 49.3,
                "vtec_tecu": 13.0,
                "samples": 4,
            },
            {
                "frame_time": "2026-01-02 00:15:00",
                "station": "alme",
                "site_lat": 54.9,
                "site_lon": 52.3,
                "ipp_lat": 55.1,
                "ipp_lon": 52.5,
                "vtec_tecu": 14.0,
                "samples": 4,
            },
            {
                "frame_time": "2026-01-02 00:15:00",
                "station": "arsk",
                "site_lat": 56.1,
                "site_lon": 49.9,
                "ipp_lat": 56.3,
                "ipp_lon": 50.1,
                "vtec_tecu": 15.0,
                "samples": 4,
            },
        ]
    )
    frame_summary["frame_time"] = pd.to_datetime(frame_summary["frame_time"])

    pipeline = TecMapConfig(grid_resolution_deg=1.0, smoothing_sigma=0.0, frame_minutes=15)
    render = TecMapRenderConfig(frame_dpi=50)

    gif_bytes = build_animation_gif_bytes(frame_summary=frame_summary, pipeline=pipeline, render=render)

    assert gif_bytes[:6] == b"GIF89a"
    assert len(gif_bytes) > 1000
    with Image.open(BytesIO(gif_bytes)) as gif:
        assert gif.n_frames == 2


def test_build_animation_gif_bytes_falls_back_when_openstreetmap_is_unreachable(monkeypatch, tmp_path):
    frame_summary = pd.DataFrame(
        [
            {
                "frame_time": "2026-01-02 00:00:00",
                "station": "aksu",
                "site_lat": 55.8,
                "site_lon": 49.1,
                "ipp_lat": 55.9,
                "ipp_lon": 49.2,
                "vtec_tecu": 10.0,
                "samples": 4,
            },
            {
                "frame_time": "2026-01-02 00:00:00",
                "station": "alme",
                "site_lat": 54.9,
                "site_lon": 52.3,
                "ipp_lat": 55.0,
                "ipp_lon": 52.4,
                "vtec_tecu": 12.0,
                "samples": 4,
            },
            {
                "frame_time": "2026-01-02 00:00:00",
                "station": "arsk",
                "site_lat": 56.1,
                "site_lon": 49.9,
                "ipp_lat": 56.2,
                "ipp_lon": 50.0,
                "vtec_tecu": 11.0,
                "samples": 4,
            },
        ]
    )
    frame_summary["frame_time"] = pd.to_datetime(frame_summary["frame_time"])

    def fake_urlopen(*_args, **_kwargs):
        raise OSError("network is unreachable")

    monkeypatch.setattr(tec_map_render_module, "urlopen", fake_urlopen)

    pipeline = TecMapConfig(grid_resolution_deg=1.0, smoothing_sigma=0.0, frame_minutes=15)
    render = TecMapRenderConfig(
        basemap_enabled=True,
        basemap_mode="openstreetmap",
        basemap_cache_root=tmp_path,
        basemap_fallback_to_plain=True,
        frame_dpi=50,
    )

    gif_bytes = build_animation_gif_bytes(frame_summary=frame_summary, pipeline=pipeline, render=render)

    assert gif_bytes[:6] == b"GIF89a"


def test_load_basemap_layer_reads_tile_server(monkeypatch):
    bounds = (49.0, 53.0, 54.0, 57.0)
    zoom = 3
    url_template = "http://tiles.test/{z}/{x}/{y}.png"

    requested_urls: list[str] = []

    def fake_urlopen(request, timeout=15):
        requested_urls.append(request.full_url)
        buffer = BytesIO()
        Image.new("RGBA", (256, 256), (200, 210, 220, 255)).save(buffer, format="PNG")

        class _Response:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, exc_type, exc, tb):
                return False

            def read(self_inner):
                return buffer.getvalue()

        return _Response()

    monkeypatch.setattr(tec_map_render_module, "urlopen", fake_urlopen)

    render = TecMapRenderConfig(
        basemap_enabled=True,
        basemap_mode="tile_server",
        basemap_zoom=zoom,
        basemap_max_tiles=32,
        basemap_tile_server_url=url_template,
    )

    layer = tec_map_render_module.load_basemap_layer(bounds, render)

    assert layer is not None
    assert layer.image.shape[0] > 0
    assert layer.image.shape[1] > 0
    assert "Tiles:" in layer.attribution
    assert requested_urls
    assert all(url.startswith("http://tiles.test/") and url.endswith(".png") for url in requested_urls)


def test_build_animation_gif_bytes_preserves_frame_local_palettes(monkeypatch):
    frame_summary = pd.DataFrame(
        [
            {
                "frame_time": "2026-01-02 00:00:00",
                "station": "aksu",
                "site_lat": 55.8,
                "site_lon": 49.1,
                "ipp_lat": 55.9,
                "ipp_lon": 49.2,
                "vtec_tecu": 10.0,
                "samples": 4,
            },
            {
                "frame_time": "2026-01-02 00:15:00",
                "station": "aksu",
                "site_lat": 55.8,
                "site_lon": 49.1,
                "ipp_lat": 55.9,
                "ipp_lon": 49.2,
                "vtec_tecu": 12.0,
                "samples": 4,
            },
        ]
    )
    frame_summary["frame_time"] = pd.to_datetime(frame_summary["frame_time"])

    def fake_render_frame_png_bytes(*, frame_time, **kwargs):
        color = "red" if pd.Timestamp(frame_time) == pd.Timestamp("2026-01-02 00:00:00") else "blue"
        image = Image.new("RGB", (40, 30), color)
        buf = BytesIO()
        image.save(buf, format="PNG")
        return buf.getvalue()

    monkeypatch.setattr(tec_map_render_module, "load_basemap_layer", lambda *args, **kwargs: None)
    monkeypatch.setattr(tec_map_render_module, "ipp_coverage_mask", lambda *args, **kwargs: True)
    monkeypatch.setattr(tec_map_render_module, "interpolate_frame", lambda *args, **kwargs: 0)
    monkeypatch.setattr(tec_map_render_module, "smooth_grid", lambda *args, **kwargs: 0)
    monkeypatch.setattr(tec_map_render_module, "render_frame_png_bytes", fake_render_frame_png_bytes)

    pipeline = TecMapConfig(grid_resolution_deg=1.0, smoothing_sigma=0.0, frame_minutes=15)
    render = TecMapRenderConfig(frame_dpi=50)
    gif_bytes = build_animation_gif_bytes(frame_summary=frame_summary, pipeline=pipeline, render=render)

    with Image.open(BytesIO(gif_bytes)) as gif:
        gif.seek(0)
        frame0 = gif.convert("RGB")
        gif.seek(1)
        frame1 = gif.convert("RGB")

    r0, _g0, b0 = frame0.getpixel((10, 10))
    r1, _g1, b1 = frame1.getpixel((10, 10))
    assert r0 > b0
    assert b1 > r1


def test_soften_coverage_mask_rounds_hard_stair_step_edges():
    pipeline = TecMapConfig(coverage_mask_smoothing_cells=1.0)
    mask = np.array(
        [
            [0, 0, 0, 0, 0],
            [0, 1, 1, 1, 0],
            [0, 1, 1, 1, 0],
            [0, 1, 1, 1, 0],
            [0, 0, 0, 0, 0],
        ],
        dtype=bool,
    )

    softened = tec_map_render_module.soften_coverage_mask(mask, pipeline)

    assert softened.shape == mask.shape
    assert softened.dtype == bool
    assert softened[2, 2]


def test_build_snapshot_plotly_json_uses_jet_colorscale():
    frame_summary = pd.DataFrame(
        [
            {
                "frame_time": "2026-01-02 00:00:00",
                "station": "aksu",
                "site_lat": 55.8,
                "site_lon": 49.1,
                "ipp_lat": 55.9,
                "ipp_lon": 49.2,
                "vtec_tecu": 10.0,
                "samples": 4,
            },
            {
                "frame_time": "2026-01-02 00:00:00",
                "station": "alme",
                "site_lat": 54.9,
                "site_lon": 52.3,
                "ipp_lat": 55.0,
                "ipp_lon": 52.4,
                "vtec_tecu": 12.0,
                "samples": 4,
            },
            {
                "frame_time": "2026-01-02 00:00:00",
                "station": "arsk",
                "site_lat": 56.1,
                "site_lon": 49.9,
                "ipp_lat": 56.2,
                "ipp_lon": 50.0,
                "vtec_tecu": 11.0,
                "samples": 4,
            },
        ]
    )
    frame_summary["frame_time"] = pd.to_datetime(frame_summary["frame_time"])

    pipeline = TecMapConfig(grid_resolution_deg=1.0, smoothing_sigma=0.0, frame_minutes=15)
    render = TecMapRenderConfig()
    payload = build_snapshot_plotly_json(
        frame_summary=frame_summary,
        frame_time=pd.Timestamp("2026-01-02 00:00:00"),
        pipeline=pipeline,
        render=render,
        include_grid=True,
    )

    contour_trace = next(trace for trace in payload["data"] if trace.get("type") == "heatmap")
    ipp_trace = next(trace for trace in payload["data"] if trace.get("name") == "IPP samples")
    assert contour_trace["colorscale"][0][1] == "#0000ff"
    assert contour_trace["colorscale"][-1][1] == "#ff0000"
    assert contour_trace["zsmooth"] == "best"
    assert ipp_trace["marker"]["colorscale"][0][1] == "#0000ff"
    assert ipp_trace["marker"]["colorscale"][-1][1] == "#ff0000"
