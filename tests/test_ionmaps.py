"""Tests for the IonMaps page (split out of Analysis in commit f0c786b)."""

from fastapi.testclient import TestClient


def test_ionmaps_page_exposes_tecmap_date_range_contract(client: TestClient):
    from app.auth import get_current_user
    from app.main import app

    class _User:
        id = 1
        username = "test_admin"
        role = "admin"
        is_admin = True

    app.dependency_overrides[get_current_user] = lambda: _User()
    try:
        response = client.get("/ionmaps")
        assert response.status_code == 200

        html = response.text
        assert 'id="tecmap-end-date"' in html
        assert 'params.set("year", yearValue);' in html
        assert 'params.set("doy", doyValue);' in html
        assert 'const effectiveDate = canonicalDate || dateValue;' in html
        assert 'params.set("date", effectiveDate);' in html
        assert 'params.set("end_date", endDateValue);' in html
    finally:
        app.dependency_overrides.pop(get_current_user, None)
