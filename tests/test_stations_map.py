"""Tests for the stations map Overview page and data endpoint."""

from fastapi.testclient import TestClient


def test_stations_map_requires_auth(client: TestClient):
    response = client.get("/stations-map", follow_redirects=False)
    assert response.status_code == 303


def test_stations_map_page_renders_with_rinex_tree(admin_client: TestClient, monkeypatch):
    import app.stations_map as stations_map_module

    async def _fake_rinex_tree(root: str, refresh: bool = False):
        assert root == "/mnt/rinex-server"
        assert refresh is False
        return [
            {
                "year": "2026_original",
                "days": [
                    {"day": "001", "stations": 5},
                    {"day": "010", "stations": 3},
                ],
            }
        ]

    monkeypatch.setattr(stations_map_module.cfg, "DATA_INDEXER_URL", "http://data-indexer:5001")
    monkeypatch.setattr(stations_map_module.cfg, "RINEX_DATA_PATH_CONTAINER", "/mnt/rinex-server")
    monkeypatch.setattr(stations_map_module.cfg, "RINEX_DATA_PATH_HOST", "")
    monkeypatch.setattr(stations_map_module, "list_rinex_server_structure_async", _fake_rinex_tree)

    response = admin_client.get("/stations-map")
    assert response.status_code == 200

    html = response.text
    assert 'id="station-year"' in html
    assert 'id="station-day"' in html
    assert "Stations Map" in html
    assert '<script src="/static/js/stations-map.js"></script>' in html
    assert "/stations-map/data?" in admin_client.get("/static/js/stations-map.js").text
    # Plotly's text layers ask MapLibre for icon "-15"; the style's sprite provides a blank one.
    for name in ("blank.json", "blank@2x.json"):
        sprite = admin_client.get(f"/static/map-sprite/{name}")
        assert sprite.status_code == 200
        assert "-15" in sprite.json()
    for name in ("blank.png", "blank@2x.png"):
        assert admin_client.get(f"/static/map-sprite/{name}").content.startswith(b"\x89PNG")
    assert "2026_original" in html
    # Radius around a station: controls, and the client-side circle drawing.
    for element_id in ("stations-map-radius-centre", "stations-map-radius-km", "stations-map-radius-show"):
        assert f'id="{element_id}"' in html
    script = admin_client.get("/static/js/stations-map.js").text
    assert "function circlePath(" in script
    assert 'plotEl.on("plotly_click", onPlotClick)' in script
    # OpenStreetMap's tile servers block tile requests that carry no Referer.
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    dashboard = admin_client.get("/")
    assert dashboard.status_code == 200
    assert dashboard.headers["Referrer-Policy"] == "same-origin"


def test_stations_map_data_returns_payload(admin_client: TestClient, monkeypatch):
    import app.stations_map as stations_map_module

    async def _fake_station_map(root: str, *, year: str, day: str = "", refresh: bool = False):
        assert root == "/mnt/rinex-server"
        assert year == "2026_original"
        assert day == "001"
        assert refresh is False
        return {
            "year": year,
            "day": day,
            "station_count": 1,
            "archive_count": 2,
            "stations": [
                {
                    "station_id": "AKSU",
                    "marker_name": "AKSU",
                    "marker_number": "12345M001",
                    "latitude_deg": 43.25,
                    "longitude_deg": 76.91,
                    "altitude_m": 802.4,
                    "archive_count": 2,
                    "days": ["001"],
                }
            ],
        }

    monkeypatch.setattr(stations_map_module.cfg, "DATA_INDEXER_URL", "http://data-indexer:5001")
    monkeypatch.setattr(stations_map_module.cfg, "RINEX_DATA_PATH_CONTAINER", "/mnt/rinex-server")
    monkeypatch.setattr(stations_map_module.cfg, "RINEX_DATA_PATH_HOST", "")
    monkeypatch.setattr(stations_map_module, "get_rinex_station_map_async", _fake_station_map)

    response = admin_client.get("/stations-map/data?year=2026_original&day=001")
    assert response.status_code == 200

    payload = response.json()
    assert payload["station_count"] == 1
    assert payload["archive_count"] == 2
    assert payload["stations"][0]["station_id"] == "AKSU"


def test_stations_map_data_reports_indexer_errors(admin_client: TestClient, monkeypatch):
    """An indexer outage or a missing folder must not look like an empty map."""
    import app.stations_map as stations_map_module
    from app.data_indexer_client import DataIndexerError

    monkeypatch.setattr(stations_map_module.cfg, "DATA_INDEXER_URL", "http://data-indexer:5001")
    monkeypatch.setattr(stations_map_module.cfg, "RINEX_DATA_PATH_CONTAINER", "/mnt/rinex-server")
    monkeypatch.setattr(stations_map_module.cfg, "RINEX_DATA_PATH_HOST", "")

    for status, detail in ((404, "RINEX year folder does not exist: 2030_original"), (502, "unavailable")):

        async def _failing(root: str, *, year: str, day: str = "", refresh: bool = False, _s=status, _d=detail):
            raise DataIndexerError(_s, _d)

        monkeypatch.setattr(stations_map_module, "get_rinex_station_map_async", _failing)
        response = admin_client.get("/stations-map/data?year=2030_original")
        assert response.status_code == status
        assert response.json()["detail"] == detail
