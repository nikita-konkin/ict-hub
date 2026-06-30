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


def test_analysis_proxy_forwards_propagation_calc(client: TestClient, monkeypatch):
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
            self.content = b'{"nt": 2e17, "b_k": 1.0, "gdd": -1.0}'
            self.headers = {"content-type": "application/json"}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, method, url, headers=None, content=None):
            assert method == "GET"
            assert url == "http://tec-backend:8000/propagation/calc?tec=20&signal_band=GPS_L1"
            return FakeResponse()

    monkeypatch.setattr(analysis_module.cfg, "ANALYSIS_API_BASE_URL", "http://tec-backend:8000")
    monkeypatch.setattr(analysis_module.httpx, "AsyncClient", FakeAsyncClient)

    app.dependency_overrides[get_current_user] = lambda: _User()
    try:
        response = client.get("/analysis/api/propagation/calc?tec=20&signal_band=GPS_L1")
        assert response.status_code == 200
        assert response.json()["b_k"] == 1.0
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_analysis_page_exposes_propagation_endpoints(client: TestClient):
    from app.auth import get_current_user
    from app.main import app

    class _User:
        id = 1
        username = "test_admin"
        role = "admin"
        is_admin = True

    app.dependency_overrides[get_current_user] = lambda: _User()
    try:
        response = client.get("/analysis")
        assert response.status_code == 200

        html = response.text
        # Dropdown options for the proxied tec-stat propagation endpoints.
        assert 'value="propagation/calc"' in html
        assert 'value="propagation/absoltec/raw"' in html
        assert 'value="propagation/absoltec/statistics"' in html
        assert 'value="propagation/tec/raw"' in html
        # New input fields + rules + URL-builder wiring.
        assert 'id="data-signal-band"' in html
        assert 'id="data-f-hz"' in html
        assert 'id="data-tec"' in html
        assert 'id="data-observable"' in html
        assert '"propagation/calc": {' in html
        assert 'required: ["data-tec"]' in html
        assert 'if (endpoint.startsWith("propagation/")) {' in html
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_analysis_page_exposes_cb_plot_contract_updates(client: TestClient):
    from app.auth import get_current_user
    from app.main import app

    class _User:
        id = 1
        username = "test_admin"
        role = "admin"
        is_admin = True

    app.dependency_overrides[get_current_user] = lambda: _User()
    try:
        response = client.get("/analysis")
        assert response.status_code == 200

        html = response.text
        assert 'value="plots/cb/raw/day-by-day"' in html
        assert 'id="plot-alpha"' in html
        assert 'id="plot-fetch-msg"' in html
        assert '"plots/cb/multi-station": {' in html
        assert 'required: ["plot-year", "plot-doy-start", "plot-doy-end"]' in html
        assert '"plots/cb/vs-tec": {' in html
        assert 'required: ["plot-year", "plot-doy-start", "plot-doy-end", "plot-station"]' in html
        assert 'endpointName === "plots/cb/raw/day-by-day"' in html
        assert 'endpointName === "plots/cb/per-station-averages")' in html
        assert 'function formatBackendError(payload, fallbackStatus)' in html
        assert 'setPlotFetchStatus("success", "OK (Fetched)")' in html
        assert 'id="tecmap-end-date"' in html
        assert 'params.set("year", yearValue);' in html
        assert 'params.set("doy", doyValue);' in html
        assert 'const effectiveDate = canonicalDate || dateValue;' in html
        assert 'params.set("date", effectiveDate);' in html
        assert 'params.set("end_date", endDateValue);' in html
    finally:
        app.dependency_overrides.pop(get_current_user, None)
