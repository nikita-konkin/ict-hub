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


def _admin_override():
    from app.auth import get_current_user
    from app.main import app

    class _User:
        id = 1
        username = "test_admin"
        role = "admin"
        is_admin = True

    app.dependency_overrides[get_current_user] = lambda: _User()
    return get_current_user, app


def test_ionmaps_page_is_localized(client: TestClient):
    """Labels, option captions, hints and script strings all go through t()."""
    get_current_user, app = _admin_override()
    try:
        en = client.get("/ionmaps?lang=en").text
        ru = client.get("/ionmaps?lang=ru").text
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    # Form chrome
    assert "Run TEC Map" in en
    assert "Построить карту ПЭС" in ru
    # A long parameter hint (the ones that were previously English-only)
    assert "Map construction principle." in en
    assert "Принцип построения карты." in ru
    # An option caption
    assert "cache only (offline; uses saved OSM tiles on server)" in en
    assert "только кэш (офлайн; сохранённые тайлы OSM на сервере)" in ru
    # A string emitted by the page script
    assert "LOSO cross-validation done." in en
    assert "Кросс-проверка LOSO завершена." in ru
    # The Russian page must not leak the English source strings
    assert "Run TEC Map" not in ru
    assert "Map construction principle." not in ru


def test_ionmaps_translations_have_full_en_ru_parity():
    from app.i18n import _TRANSLATIONS

    en = {k for k in _TRANSLATIONS["en"] if k.startswith("ionmaps")}
    ru = {k for k in _TRANSLATIONS["ru"] if k.startswith("ionmaps")}
    assert en - ru == set(), f"missing Russian translations: {sorted(en - ru)}"
    assert ru - en == set(), f"Russian keys with no English source: {sorted(ru - en)}"


def test_ionmaps_strings_avoid_quotes_that_jinja_escapes(client: TestClient):
    """Straight quotes in a translation render as &#39;/&#34; inside the page
    script's string literals, where they are visible to the user. Use
    typographic quotes instead."""
    get_current_user, app = _admin_override()
    try:
        for lang in ("en", "ru"):
            html = client.get(f"/ionmaps?lang={lang}").text
            body = html[html.index("page-header"):]
            assert "&#39;" not in body, f"{lang}: escaped apostrophe in rendered page"
            assert "&#34;" not in body, f"{lang}: escaped quote in rendered page"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
