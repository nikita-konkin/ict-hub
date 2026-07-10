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


def _simple_frame_summary() -> pd.DataFrame:
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
                "vtec_tecu": 30.0,
                "samples": 4,
            },
            {
                "frame_time": "2026-01-02 00:00:00",
                "station": "arsk",
                "site_lat": 56.1,
                "site_lon": 49.9,
                "ipp_lat": 56.2,
                "ipp_lon": 50.0,
                "vtec_tecu": 20.0,
                "samples": 4,
            },
        ]
    )
    frame_summary["frame_time"] = pd.to_datetime(frame_summary["frame_time"])
    return frame_summary


def test_gdd_and_bk_field_transforms_match_tec_stat_formulas():
    from app.tec_map_fields import compute_bk_grid, compute_gdd_grid, resolve_signal_band

    band, f_hz = resolve_signal_band("GPS_L1")
    assert band == "gps_l1"
    assert abs(f_hz - 1575.42e6) < 1.0

    gdd = compute_gdd_grid(np.array([[30.0, 6.0], [0.0, np.nan]]), f_hz)
    # 30 TECU @ L1 -> |D| = 9.83 ns/GHz; 6 TECU -> 1.97 ns/GHz (tec-stat formulas)
    assert abs(gdd[0, 0] - 9.83) < 0.01
    assert abs(gdd[0, 1] - 1.966) < 0.01
    assert gdd[1, 0] == 0.0
    assert np.isnan(gdd[1, 1])

    b_k = compute_bk_grid(np.array([[30.0, 0.01]]), f_hz)
    # 30 TECU @ L1 -> B_k = 124.3 MHz; below-threshold VTEC is masked.
    assert abs(b_k[0, 0] - 124.35) < 0.5
    assert np.isnan(b_k[0, 1])

    try:
        resolve_signal_band("nonexistent_band")
    except ValueError:
        pass
    else:
        raise AssertionError("resolve_signal_band must reject unknown bands")


def test_build_animation_gif_bytes_renders_gdd_field():
    pipeline = TecMapConfig(grid_resolution_deg=1.0, smoothing_sigma=0.0, frame_minutes=15)
    render = TecMapRenderConfig(field="gdd", signal_band="gps_l5", frame_dpi=50)

    gif_bytes = build_animation_gif_bytes(
        frame_summary=_simple_frame_summary(), pipeline=pipeline, render=render
    )

    assert gif_bytes[:6] == b"GIF89a"
    assert len(gif_bytes) > 1000


def test_build_snapshot_plotly_json_gdd_uses_transformed_scale_and_points():
    pipeline = TecMapConfig(grid_resolution_deg=1.0, smoothing_sigma=0.0, frame_minutes=15)
    render = TecMapRenderConfig(field="gdd", signal_band="gps_l1")

    payload = build_snapshot_plotly_json(
        frame_summary=_simple_frame_summary(),
        frame_time=pd.Timestamp("2026-01-02 00:00:00"),
        pipeline=pipeline,
        render=render,
        include_grid=True,
    )

    heatmap = next(trace for trace in payload["data"] if trace.get("type") == "heatmap")
    assert "ns/GHz" in heatmap["colorbar"]["title"]["text"] or "ns/GHz" in str(heatmap["colorbar"]["title"])

    ipp_trace = next(trace for trace in payload["data"] if trace.get("name") == "IPP samples")
    colors = ipp_trace["marker"]["color"]
    # Per-IPP values are GDD-transformed VTEC: 30 TECU -> ~9.83 ns/GHz.
    assert any(abs(float(c) - 9.83) < 0.05 for c in colors if c is not None)


def test_upsample_grid_preserves_nan_mask_and_values():
    from app.tec_map_render import upsample_coordinates, upsample_grid

    grid = np.array(
        [
            [1.0, 2.0, np.nan],
            [3.0, 4.0, np.nan],
            [np.nan, np.nan, np.nan],
        ]
    )
    upsampled = upsample_grid(grid, 2)
    assert upsampled.shape == (6, 6)
    # Valid corner keeps its value; interior stays within the input range.
    assert abs(upsampled[0, 0] - 1.0) < 1e-9
    finite = upsampled[np.isfinite(upsampled)]
    assert finite.min() >= 1.0 - 1e-9 and finite.max() <= 4.0 + 1e-9
    # The all-NaN corner must stay masked.
    assert np.isnan(upsampled[-1, -1])

    lon, lat = np.meshgrid(np.array([0.0, 1.0, 2.0]), np.array([10.0, 11.0, 12.0]))
    up_lon, up_lat = upsample_coordinates(lon, lat, 2)
    assert up_lon.shape == (6, 6)
    assert abs(up_lon[0, 0] - 0.0) < 1e-9 and abs(up_lon[0, -1] - 2.0) < 1e-9
    assert abs(up_lat[0, 0] - 10.0) < 1e-9 and abs(up_lat[-1, 0] - 12.0) < 1e-9


def test_build_animation_gif_bytes_high_quality_and_upsample():
    pipeline = TecMapConfig(grid_resolution_deg=1.0, smoothing_sigma=0.0, frame_minutes=15)
    render = TecMapRenderConfig(frame_dpi=50, gif_high_quality=True, upsample_factor=2)

    gif_bytes = build_animation_gif_bytes(
        frame_summary=_simple_frame_summary(), pipeline=pipeline, render=render
    )

    assert gif_bytes[:6] == b"GIF89a"
    assert len(gif_bytes) > 1000


def test_build_animation_bytes_mp4_when_imageio_available():
    try:
        import imageio.v2  # noqa: F401
        import imageio_ffmpeg  # noqa: F401
    except ImportError:
        import pytest

        pytest.skip("imageio/imageio-ffmpeg not installed")

    pipeline = TecMapConfig(grid_resolution_deg=1.0, smoothing_sigma=0.0, frame_minutes=15)
    render = TecMapRenderConfig(frame_dpi=50, animation_format="mp4")

    video_bytes = build_animation_gif_bytes(
        frame_summary=_simple_frame_summary(), pipeline=pipeline, render=render
    )

    # MP4 container: 'ftyp' box near the start of the file.
    assert b"ftyp" in video_bytes[:64]
    assert len(video_bytes) > 1000


def test_color_overrides_apply_to_animation_color_scale():
    from app.tec_map_render import TecMapRenderConfig as _RC
    from app.tec_map_render import _apply_color_overrides

    render = _RC(color_min=0.0, color_max=50.0)
    assert _apply_color_overrides(5.0, 25.0, render) == (0.0, 50.0)

    bad = _RC(color_min=10.0, color_max=1.0)
    try:
        _apply_color_overrides(5.0, 25.0, bad)
    except ValueError:
        pass
    else:
        raise AssertionError("color_min >= color_max must raise ValueError")


def test_build_frame_image_bytes_renders_png_and_svg():
    from app.tec_map_render import build_frame_image_bytes

    pipeline = TecMapConfig(grid_resolution_deg=1.0, smoothing_sigma=0.0, frame_minutes=15)
    render = TecMapRenderConfig(field="gdd", signal_band="gps_l1", frame_dpi=150, upsample_factor=2)

    png_bytes = build_frame_image_bytes(
        frame_summary=_simple_frame_summary(),
        frame_time=pd.Timestamp("2026-01-02 00:00:00"),
        pipeline=pipeline,
        render=render,
        image_format="png",
    )
    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n"
    with Image.open(BytesIO(png_bytes)) as image:
        # 150 dpi must give a visibly larger canvas than the 50 dpi GIF frames.
        assert image.width > 700

    svg_bytes = build_frame_image_bytes(
        frame_summary=_simple_frame_summary(),
        frame_time=pd.Timestamp("2026-01-02 00:00:00"),
        pipeline=pipeline,
        render=render,
        image_format="svg",
    )
    assert b"<svg" in svg_bytes[:1024]


def test_coverage_edge_is_round_at_render_resolution():
    from app.tec_map_render import compute_field_grid, great_circle_distance_km

    frame = pd.DataFrame(
        [
            {
                "frame_time": pd.Timestamp("2026-01-02 00:00:00"),
                "station": "aksu",
                "site_lat": 55.0,
                "site_lon": 50.0,
                "ipp_lat": 55.0,
                "ipp_lon": 50.0,
                "vtec_tecu": 10.0,
                "samples": 4,
            }
        ]
    )
    pipeline = TecMapConfig(grid_resolution_deg=1.0, smoothing_sigma=0.0)
    grid_lon, grid_lat = np.meshgrid(np.arange(45.0, 55.5, 1.0), np.arange(50.0, 60.5, 1.0))

    grid, plot_lon, plot_lat = compute_field_grid(frame, grid_lon, grid_lat, pipeline, None, upsample_factor=4)
    assert grid.shape == plot_lon.shape == plot_lat.shape
    assert grid.shape[0] == grid_lon.shape[0] * 4

    distances = great_circle_distance_km(plot_lon, plot_lat, np.array([50.0]), np.array([55.0]))[..., 0]
    finite = np.isfinite(grid)
    # Cells well inside the 300-km circle are covered; cells well outside are cut.
    # The margin absorbs the soft mask edge (sigma 0.85 base cells) and the fine
    # cell size (~0.25 deg ≈ 28 km).
    assert finite[distances <= 220.0].all()
    assert not finite[distances >= 380.0].any()
    # The boundary must follow the circle, not coarse 1-deg cells: covered-cell
    # counts per row change gradually (no >40%-of-width jumps between rows).
    row_counts = finite.sum(axis=1)
    inside = row_counts > 0
    jumps = np.abs(np.diff(row_counts[inside]))
    assert jumps.max() <= max(2, int(0.4 * finite.shape[1]))


def test_kriging_reproduces_smooth_field_and_fits_variogram():
    from app.tec_map_kriging import fit_exponential_variogram, kriging_interpolate, pairwise_distances_km

    rng = np.random.default_rng(42)
    pts_lon = rng.uniform(45.0, 55.0, 40)
    pts_lat = rng.uniform(50.0, 60.0, 40)
    # Smooth large-scale field: linear trend in lon/lat.
    truth = lambda lon, lat: 10.0 + 0.8 * (lon - 50.0) + 0.5 * (lat - 55.0)
    values = truth(pts_lon, pts_lat)

    d = pairwise_distances_km(pts_lon, pts_lat)
    nugget, sill, range_km = fit_exponential_variogram(d, values)
    assert nugget >= 0.0 and sill > 0.0 and 50.0 <= range_km <= 2000.0

    grid_lon, grid_lat = np.meshgrid(np.linspace(46.0, 54.0, 17), np.linspace(51.0, 59.0, 17))
    predicted = kriging_interpolate(pts_lon, pts_lat, values, grid_lon, grid_lat)
    assert predicted.shape == grid_lon.shape
    assert np.isfinite(predicted).all()
    # Interior prediction error stays well below the field's dynamic range (~10 TECU).
    err = np.abs(predicted - truth(grid_lon, grid_lat))
    assert float(np.median(err)) < 1.0

    # Constant field must be reproduced (kriging weights sum to 1).
    const = kriging_interpolate(pts_lon, pts_lat, np.full(40, 7.5), grid_lon, grid_lat)
    assert np.allclose(const, 7.5, atol=1e-6)


def test_interpolate_frame_dispatches_to_kriging():
    from app.tec_map_render import interpolate_frame

    rng = np.random.default_rng(1)
    rows = []
    for i in range(12):
        rows.append({
            "frame_time": pd.Timestamp("2026-01-02 00:00:00"),
            "station": f"st{i:02d}",
            "site_lat": 55.0, "site_lon": 50.0,
            "ipp_lat": float(rng.uniform(52.0, 58.0)),
            "ipp_lon": float(rng.uniform(46.0, 54.0)),
            "vtec_tecu": float(rng.uniform(8.0, 12.0)),
            "samples": 4,
        })
    frame = pd.DataFrame(rows)
    grid_lon, grid_lat = np.meshgrid(np.linspace(45.0, 55.0, 11), np.linspace(51.0, 59.0, 9))

    linear_pipeline = TecMapConfig(interpolation_method="linear", smoothing_sigma=0.0)
    kriging_pipeline = TecMapConfig(interpolation_method="kriging", smoothing_sigma=0.0)
    full = np.ones(grid_lon.shape, dtype=bool)
    linear_grid = interpolate_frame(frame, grid_lon, grid_lat, linear_pipeline, coverage_mask=full)
    kriging_grid = interpolate_frame(frame, grid_lon, grid_lat, kriging_pipeline, coverage_mask=full)

    assert np.isfinite(kriging_grid).all()
    # Methods must differ (kriging has no nearest-neighbour plateaus) but stay
    # in the same physical range.
    assert not np.allclose(linear_grid, kriging_grid)
    assert kriging_grid.min() > 4.0 and kriging_grid.max() < 16.0

    # Small frames fall back to the linear path without raising.
    tiny = frame.head(4)
    fallback_grid = interpolate_frame(tiny, grid_lon, grid_lat, kriging_pipeline, coverage_mask=full)
    assert np.isfinite(fallback_grid).all()


def test_build_animation_gif_bytes_renders_with_kriging():
    pipeline = TecMapConfig(grid_resolution_deg=1.0, smoothing_sigma=0.0, frame_minutes=15, interpolation_method="kriging")
    render = TecMapRenderConfig(frame_dpi=50)
    gif_bytes = build_animation_gif_bytes(frame_summary=_simple_frame_summary(), pipeline=pipeline, render=render)
    assert gif_bytes[:6] == b"GIF89a"


def _loso_frame_summary(n_stations: int = 12, *, plane: bool = True, seed: int = 7) -> pd.DataFrame:
    """Synthetic single-frame summary: n stations on a smooth (planar) VTEC field."""
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n_stations):
        lon = float(rng.uniform(46.0, 54.0))
        lat = float(rng.uniform(52.0, 58.0))
        vtec = 10.0 + 0.8 * (lon - 50.0) + 0.5 * (lat - 55.0) if plane else 7.5
        rows.append({
            "frame_time": pd.Timestamp("2026-01-02 09:00:00"),
            "station": f"st{i:02d}",
            "site_lat": lat, "site_lon": lon,
            "ipp_lat": lat, "ipp_lon": lon,
            "vtec_tecu": float(vtec),
            "samples": 4,
        })
    return pd.DataFrame(rows)


def test_loso_cross_validation_recovers_smooth_field():
    from app.tec_map_validation import loso_cross_validate, summarize_validation

    frame_summary = _loso_frame_summary(12, plane=True)
    for method in ("linear", "kriging", "lpi"):
        pipeline = TecMapConfig(interpolation_method=method, smoothing_sigma=0.0)
        cv = loso_cross_validate(frame_summary, pipeline)
        assert len(cv) == 12
        assert set(cv["station"]) == set(frame_summary["station"])
        summary = summarize_validation(cv)
        overall = summary["overall"]
        # A planar field must be reconstructed to well under the quiet-time
        # target of 0.5-1 TECU (interior points are interpolated exactly;
        # hull points extrapolate via nearest/mean and dominate the RMSE).
        assert overall["n"] >= 10
        assert overall["rmse_tecu"] < 2.0, f"{method}: rmse={overall['rmse_tecu']}"
        assert abs(overall["bias_tecu"]) < 1.5

    # LPI fits a local plane, so a planar field is reproduced essentially
    # exactly even at hull points where linear/kriging degrade.
    lpi_metrics = summarize_validation(
        loso_cross_validate(frame_summary, TecMapConfig(interpolation_method="lpi", smoothing_sigma=0.0))
    )["overall"]
    assert lpi_metrics["rmse_tecu"] < 0.05, f"lpi: rmse={lpi_metrics['rmse_tecu']}"

    # Constant field: every prediction equals the constant, errors ~0.
    const_summary = _loso_frame_summary(12, plane=False)
    for method in ("linear", "kriging", "lpi"):
        pipeline = TecMapConfig(interpolation_method=method, smoothing_sigma=0.0)
        metrics = summarize_validation(loso_cross_validate(const_summary, pipeline))["overall"]
        assert metrics["rmse_tecu"] < 1e-6, f"{method}: rmse={metrics['rmse_tecu']}"


def test_loso_flags_out_of_coverage_points():
    from app.tec_map_validation import loso_cross_validate, summarize_validation

    frame_summary = _loso_frame_summary(8, plane=True)
    # One remote station: > 300 km from every other IPP (~6 deg of latitude).
    remote = frame_summary.iloc[[0]].copy()
    remote["station"] = "far1"
    remote["ipp_lat"] = 64.0
    remote["site_lat"] = 64.0
    frame_summary = pd.concat([frame_summary, remote], ignore_index=True)

    pipeline = TecMapConfig(interpolation_method="linear", smoothing_sigma=0.0)
    cv = loso_cross_validate(frame_summary, pipeline)
    far_rows = cv[cv["station"] == "far1"]
    assert len(far_rows) == 1
    assert not bool(far_rows["in_coverage"].iloc[0])

    summary = summarize_validation(cv)
    assert summary["overall"]["n_out_of_coverage"] == 1
    # The out-of-coverage point must not contaminate the headline metrics.
    assert summary["overall"]["n"] == len(cv) - 1


def test_predict_at_points_dispatch_and_small_frame_fallback():
    from app.tec_map_validation import predict_at_points

    rng = np.random.default_rng(3)
    lon = rng.uniform(46.0, 54.0, 12)
    lat = rng.uniform(52.0, 58.0, 12)
    values = 10.0 + rng.normal(0.0, 2.0, 12)
    target_lon = np.array([50.0])
    target_lat = np.array([55.0])

    linear = predict_at_points(lon, lat, values, target_lon, target_lat, TecMapConfig(interpolation_method="linear"))
    kriging = predict_at_points(lon, lat, values, target_lon, target_lat, TecMapConfig(interpolation_method="kriging"))
    lpi = predict_at_points(lon, lat, values, target_lon, target_lat, TecMapConfig(interpolation_method="lpi"))
    assert np.isfinite(linear).all() and np.isfinite(kriging).all() and np.isfinite(lpi).all()
    assert abs(float(linear[0]) - float(kriging[0])) > 1e-9
    assert abs(float(lpi[0]) - float(linear[0])) > 1e-9

    # 2 training points: nearest fallback, never raises.
    for method in ("kriging", "lpi"):
        tiny = predict_at_points(lon[:2], lat[:2], values[:2], target_lon, target_lat, TecMapConfig(interpolation_method=method))
        assert np.isfinite(tiny).all()


def test_frame_accuracy_label_format_and_minimum_size():
    from app.tec_map_validation import frame_accuracy_label

    pipeline = TecMapConfig(interpolation_method="linear", smoothing_sigma=0.0)
    frame = _loso_frame_summary(10, plane=True)
    label = frame_accuracy_label(frame, pipeline)
    assert label is not None
    assert label.startswith("LOSO RMSE ") and "TECU" in label and "(n=" in label

    # Below MIN_STATIONS_FOR_LOSO no label is produced.
    assert frame_accuracy_label(frame.head(3), pipeline) is None


def test_render_frame_with_accuracy_annotation():
    from app.tec_map_render import build_frame_image_bytes

    pipeline = TecMapConfig(grid_resolution_deg=1.0, smoothing_sigma=0.0, frame_minutes=15)
    frame_summary = _loso_frame_summary(10, plane=True)

    plain = build_frame_image_bytes(
        frame_summary=frame_summary,
        frame_time=pd.Timestamp("2026-01-02 09:00:00"),
        pipeline=pipeline,
        render=TecMapRenderConfig(frame_dpi=60),
    )
    annotated = build_frame_image_bytes(
        frame_summary=frame_summary,
        frame_time=pd.Timestamp("2026-01-02 09:00:00"),
        pipeline=pipeline,
        render=TecMapRenderConfig(frame_dpi=60, show_accuracy=True),
    )
    assert annotated[:8] == b"\x89PNG\r\n\x1a\n"
    assert annotated != plain

    # SVG keeps the label as text — verify the actual annotation content.
    annotated_svg = build_frame_image_bytes(
        frame_summary=frame_summary,
        frame_time=pd.Timestamp("2026-01-02 09:00:00"),
        pipeline=pipeline,
        render=TecMapRenderConfig(frame_dpi=60, show_accuracy=True),
        image_format="svg",
    )
    assert b"LOSO RMSE" in annotated_svg


def test_lpi_interpolation_plane_constant_and_degenerate_geometry():
    from app.tec_map_lpi import lpi_interpolate
    from app.tec_map_render import interpolate_frame

    rng = np.random.default_rng(11)
    pts_lon = rng.uniform(45.0, 55.0, 30)
    pts_lat = rng.uniform(50.0, 60.0, 30)
    truth = lambda lon, lat: 10.0 + 0.8 * (lon - 50.0) + 0.5 * (lat - 55.0)
    values = truth(pts_lon, pts_lat)

    grid_lon, grid_lat = np.meshgrid(np.linspace(46.0, 54.0, 17), np.linspace(51.0, 59.0, 17))
    predicted = lpi_interpolate(pts_lon, pts_lat, values, grid_lon, grid_lat)
    assert predicted.shape == grid_lon.shape
    assert np.isfinite(predicted).all()
    # A degree-1 fit reproduces a plane almost exactly across the whole grid.
    assert float(np.abs(predicted - truth(grid_lon, grid_lat)).max()) < 0.05

    # Constant field -> the constant everywhere (weights sum out).
    const = lpi_interpolate(pts_lon, pts_lat, np.full(30, 7.5), grid_lon, grid_lat)
    assert np.allclose(const, 7.5, atol=1e-6)

    # Collinear stations (degenerate east-west geometry): the slope ridge must
    # keep the solve stable and predictions within the sample range.
    col_lon = np.linspace(46.0, 54.0, 10)
    col_lat = np.full(10, 55.0)
    col_values = np.linspace(5.0, 15.0, 10)
    col = lpi_interpolate(col_lon, col_lat, col_values, grid_lon, grid_lat)
    assert np.isfinite(col).all()
    assert col.min() > 0.0 and col.max() < 25.0

    # A target far outside the cloud falls back to the nearest sample.
    far = lpi_interpolate(pts_lon, pts_lat, values, np.array([120.0]), np.array([10.0]))
    assert np.isfinite(far).all()

    # interpolate_frame dispatch: lpi differs from linear, stays in range.
    frame = pd.DataFrame({
        "frame_time": pd.Timestamp("2026-01-02 00:00:00"),
        "station": [f"st{i:02d}" for i in range(30)],
        "site_lat": pts_lat, "site_lon": pts_lon,
        "ipp_lat": pts_lat, "ipp_lon": pts_lon,
        "vtec_tecu": values, "samples": 4,
    })
    full = np.ones(grid_lon.shape, dtype=bool)
    lpi_grid = interpolate_frame(frame, grid_lon, grid_lat, TecMapConfig(interpolation_method="lpi", smoothing_sigma=0.0), coverage_mask=full)
    linear_grid = interpolate_frame(frame, grid_lon, grid_lat, TecMapConfig(interpolation_method="linear", smoothing_sigma=0.0), coverage_mask=full)
    assert not np.allclose(lpi_grid, linear_grid)
    assert np.isfinite(lpi_grid).all()

    # Tiny frame falls back to the linear path without raising.
    tiny_grid = interpolate_frame(frame.head(3), grid_lon, grid_lat, TecMapConfig(interpolation_method="lpi", smoothing_sigma=0.0), coverage_mask=full)
    assert np.isfinite(tiny_grid).all()


def test_lpi_degree2_quadric_and_fallback_ladder():
    from app.tec_map_lpi import lpi_interpolate
    from app.tec_map_render import interpolate_frame

    rng = np.random.default_rng(21)
    pts_lon = rng.uniform(45.0, 55.0, 60)
    pts_lat = rng.uniform(50.0, 60.0, 60)
    # Curved field: a dome peaking at (50, 55) — like the midday TEC bump.
    curved = lambda lon, lat: 30.0 - 0.35 * (lon - 50.0) ** 2 - 0.5 * (lat - 55.0) ** 2
    values = curved(pts_lon, pts_lat)

    # Dense interior: degree 2 must track the curvature clearly better.
    grid_lon, grid_lat = np.meshgrid(np.linspace(48.0, 52.0, 9), np.linspace(53.0, 57.0, 9))
    truth = curved(grid_lon, grid_lat)
    err1 = float(np.abs(lpi_interpolate(pts_lon, pts_lat, values, grid_lon, grid_lat, degree=1) - truth).mean())
    err2 = float(np.abs(lpi_interpolate(pts_lon, pts_lat, values, grid_lon, grid_lat, degree=2) - truth).mean())
    assert err2 < err1 * 0.5, f"degree2 err={err2:.3f} vs degree1 err={err1:.3f}"

    # A plane is inside the quadric's span: degree 2 reproduces it exactly too.
    plane = 10.0 + 0.8 * (pts_lon - 50.0) + 0.5 * (pts_lat - 55.0)
    plane_truth = 10.0 + 0.8 * (grid_lon - 50.0) + 0.5 * (grid_lat - 55.0)
    plane_pred = lpi_interpolate(pts_lon, pts_lat, plane, grid_lon, grid_lat, degree=2)
    assert float(np.abs(plane_pred - plane_truth).max()) < 0.05

    # Sparse neighbourhood (5 points < MIN_POINTS_FOR_QUADRATIC): silently
    # drops to the degree-1 fit — identical result, no crash.
    few = slice(0, 5)
    d1 = lpi_interpolate(pts_lon[few], pts_lat[few], values[few], grid_lon, grid_lat, degree=1)
    d2 = lpi_interpolate(pts_lon[few], pts_lat[few], values[few], grid_lon, grid_lat, degree=2)
    assert np.allclose(d1, d2)

    # Dispatch: TecMapConfig.lpi_degree reaches the interpolator.
    frame = pd.DataFrame({
        "frame_time": pd.Timestamp("2026-01-02 00:00:00"),
        "station": [f"st{i:02d}" for i in range(60)],
        "site_lat": pts_lat, "site_lon": pts_lon,
        "ipp_lat": pts_lat, "ipp_lon": pts_lon,
        "vtec_tecu": values, "samples": 4,
    })
    full = np.ones(grid_lon.shape, dtype=bool)
    g1 = interpolate_frame(frame, grid_lon, grid_lat, TecMapConfig(interpolation_method="lpi", lpi_degree=1, smoothing_sigma=0.0), coverage_mask=full)
    g2 = interpolate_frame(frame, grid_lon, grid_lat, TecMapConfig(interpolation_method="lpi", lpi_degree=2, smoothing_sigma=0.0), coverage_mask=full)
    assert not np.allclose(g1, g2)
    assert float(np.abs(g2 - truth).mean()) < float(np.abs(g1 - truth).mean())


def test_show_params_caption_appears_under_map():
    from app.tec_map_render import build_frame_image_bytes, pipeline_params_label

    pipeline = TecMapConfig(
        grid_resolution_deg=0.5,
        smoothing_sigma=2.0,
        frame_minutes=15,
        interpolation_method="kriging",
        normalize_stations="always",
    )
    render = TecMapRenderConfig(frame_dpi=60, upsample_factor=2, show_params=True)

    label = pipeline_params_label(pipeline, render)
    assert label.startswith("Model: ")
    for token in ("grid 0.5°", "σg 2 cell", "ΔT 15 min", "h_ion 350 km",
                  "θ_min 20°", "R_cov 300 km", "interp kriging",
                  "normalize always", "upsample 2×"):
        assert token in label, f"missing {token!r} in {label!r}"

    svg = build_frame_image_bytes(
        frame_summary=_loso_frame_summary(10, plane=True),
        frame_time=pd.Timestamp("2026-01-02 09:00:00"),
        pipeline=pipeline,
        render=render,
        image_format="svg",
    )
    assert b"Model:" in svg

    # Plotly snapshot carries the caption as an annotation.
    payload = build_snapshot_plotly_json(
        frame_summary=_loso_frame_summary(10, plane=True),
        frame_time=pd.Timestamp("2026-01-02 09:00:00"),
        pipeline=pipeline,
        render=TecMapRenderConfig(show_params=True),
        include_grid=True,
    )
    annotations = payload["layout"].get("annotations", [])
    assert any(str(a.get("text", "")).startswith("Model:") for a in annotations)


def test_glonass_signal_bands_resolve_and_render_labels():
    from app.tec_map_fields import compute_bk_grid, resolve_signal_band, signal_band_label

    band, f_hz = resolve_signal_band("GLONASS_L1")
    assert band == "glonass_l1"
    assert abs(f_hz - 1602.0e6) < 1.0
    assert signal_band_label(band) == "GLONASS L1"

    _, f_l2 = resolve_signal_band("glonass_l2")
    assert abs(f_l2 - 1246.0e6) < 1.0
    _, f_l3 = resolve_signal_band("glonass_l3")
    assert abs(f_l3 - 1202.025e6) < 1.0

    # B_k scales as f^(3/2): GLONASS L1 sits slightly above GPS L1.
    b_k_glo = compute_bk_grid(np.array([[30.0]]), f_hz)[0, 0]
    b_k_gps = compute_bk_grid(np.array([[30.0]]), 1575.42e6)[0, 0]
    assert b_k_glo > b_k_gps
    assert abs(b_k_glo / b_k_gps - (1602.0 / 1575.42) ** 1.5) < 1e-6


def test_snapshot_plotly_json_includes_accuracy_annotation():
    frame_summary = _loso_frame_summary(10, plane=True)
    pipeline = TecMapConfig(grid_resolution_deg=1.0, smoothing_sigma=0.0, frame_minutes=15)

    payload = build_snapshot_plotly_json(
        frame_summary=frame_summary,
        frame_time=pd.Timestamp("2026-01-02 09:00:00"),
        pipeline=pipeline,
        render=TecMapRenderConfig(show_accuracy=True),
        include_grid=True,
    )
    annotations = payload["layout"].get("annotations", [])
    assert any("LOSO RMSE" in str(a.get("text", "")) for a in annotations)
