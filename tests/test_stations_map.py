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
    assert 'Stations Map' in html
    assert "/stations-map/data?" in html
    assert "2026_original" in html


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
