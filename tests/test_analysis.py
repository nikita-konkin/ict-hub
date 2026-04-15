"""Tests for analysis router helpers and index-options endpoint."""

from unittest.mock import AsyncMock

from fastapi.testclient import TestClient


def test_analysis_index_options_requires_auth(client: TestClient):
    response = client.get("/analysis/index-options", follow_redirects=False)
    assert response.status_code == 303


def test_analysis_index_options_returns_data(client: TestClient, monkeypatch):
    import app.analysis as analysis_module
    from app.auth import get_current_user
    from app.main import app

    class _User:
        id = 1
        username = "test_admin"
        role = "admin"
        is_admin = True

    async def _fake_parquet_tree_async(root: str):
        if "abstec" in root:
            return [
                {
                    "year": "2026",
                    "days": [
                        {"day": "001", "stations": ["aksu", "alex"], "satellites": ["E07"]},
                        {"day": "010", "stations": ["arsk"], "satellites": ["G12"]},
                    ],
                },
                {
                    "year": "2025",
                    "days": [{"day": "365", "stations": ["krsn"], "satellites": []}],
                },
            ]

        return [
            {
                "year": "2026",
                "days": [
                    {"day": "001", "stations": ["aksu"], "satellites": ["E07", "G12"]},
                    {"day": "010", "stations": ["arsk"], "satellites": ["E08"]},
                ],
            }
        ]

    monkeypatch.setattr(analysis_module.cfg, "PARQUET_OUTPUT_ABSTEC_DATA_PATH_CONTAINER", "/mnt/abstec-parquet")
    monkeypatch.setattr(analysis_module.cfg, "PARQUET_OUTPUT_ABSTEC_DATA_PATH_HOST", "")
    monkeypatch.setattr(analysis_module.cfg, "PARQUET_OUTPUT_TECSUITE_DATA_PATH_CONTAINER", "/mnt/tecsuite-parquet")
    monkeypatch.setattr(analysis_module.cfg, "PARQUET_OUTPUT_TECSUITE_DATA_PATH_HOST", "")
    monkeypatch.setattr(analysis_module, "list_parquet_satellite_structure_async", _fake_parquet_tree_async)

    app.dependency_overrides[get_current_user] = lambda: _User()
    try:
        response = client.get("/analysis/index-options")
        assert response.status_code == 200

        payload = response.json()
        assert payload["absoltec"]["years"] == ["2026", "2025"]
        assert payload["absoltec"]["doysByYear"]["2026"] == ["001", "010"]
        assert payload["absoltec"]["stationsByYearDoy"]["2026"]["001"] == ["aksu", "alex"]
        assert payload["absoltec"]["satellitesByYearDoy"]["2026"]["001"] == ["E07"]

        assert payload["tec"]["years"] == ["2026"]
        assert payload["tec"]["doysByYear"]["2026"] == ["001", "010"]
        assert payload["tec"]["stationsByYearDoy"]["2026"]["001"] == ["aksu"]
        assert payload["tec"]["satellitesByYearDoy"]["2026"]["001"] == ["E07", "G12"]
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_analysis_proxy_forwards_api_request(client: TestClient, monkeypatch):
    import app.analysis as analysis_module
    from app.auth import get_current_user
    from app.main import app

    class _User:
        id = 1
        username = "test_admin"
        role = "admin"
        is_admin = True

    class FakeResponse:
        def __init__(self):
            self.status_code = 200
            self.content = b'{"result": "ok"}'
            self.headers = {
                "content-type": "application/json",
                "cache-control": "no-store",
            }

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, method, url, headers=None, content=None):
            assert method == "GET"
            assert url == "http://tec-backend:8000/absoltec/raw?year=2026&doy=1&station=aksu"
            assert headers is not None
            assert "host" not in {k.lower() for k in headers}
            return FakeResponse()

    monkeypatch.setattr(analysis_module.cfg, "ANALYSIS_API_BASE_URL", "http://tec-backend:8000")
    monkeypatch.setattr(analysis_module.httpx, "AsyncClient", FakeAsyncClient)

    app.dependency_overrides[get_current_user] = lambda: _User()
    try:
        response = client.get("/analysis/api/absoltec/raw?year=2026&doy=1&station=aksu")
        assert response.status_code == 200
        assert response.json() == {"result": "ok"}
        assert response.headers["cache-control"] == "no-store"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
