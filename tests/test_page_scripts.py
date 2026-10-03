"""
test_page_scripts.py — Page scripts in app/static/js and their data islands.

Page scripts are static files, so translated strings reach them through a JSON
data island rendered with t_dict() right before the <script src> tag. A key a
script looks up with T("...") but its island does not list shows up on the
page as the raw key; these tests catch that without a browser.
"""

import importlib.util
import re
from pathlib import Path

import pytest

from app.i18n import _TRANSLATIONS

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "app" / "templates"
STATIC_JS = ROOT / "app" / "static" / "js"

# t_dict(...) island, any further data islands, then the script it feeds.
_ISLAND_THEN_SCRIPT = re.compile(
    r"\{\{ t_dict\((?P<args>[^)]*)\) \| tojson \}\}</script>"
    r"(?:\s*<script id=\"[^\"]+\" type=\"application/json\">.*?</script>)*"
    r"\s*<script src=\"/static/js/(?P<script>[\w-]+\.js)\"></script>",
    re.S,
)
_T_CALL = re.compile(r"\bT\(\"([a-z0-9_]+)\"\)")


def _islands() -> dict[str, set[str]]:
    """Script file name → translation keys its island provides."""
    islands: dict[str, set[str]] = {}
    for template in TEMPLATES.glob("*.html"):
        for match in _ISLAND_THEN_SCRIPT.finditer(template.read_text(encoding="utf-8")):
            keys = set(re.findall(r"'([a-z0-9_]+)'", match.group("args")))
            assert match.group("script") not in islands, f"{match.group('script')} has two islands"
            islands[match.group("script")] = keys
    return islands


@pytest.mark.parametrize("script", sorted(p.name for p in STATIC_JS.glob("*.js")))
def test_script_translation_keys_match_its_island(script):
    used = set(_T_CALL.findall((STATIC_JS / script).read_text(encoding="utf-8")))
    provided = _islands().get(script, set())
    assert used - provided == set(), f"{script} uses keys its island does not render"
    assert provided - used == set(), f"{script}'s island renders keys the script never uses"


def test_island_keys_are_translated():
    keys = set().union(*_islands().values())
    assert keys, "no data islands found"
    for lang in ("en", "ru"):
        missing = sorted(key for key in keys if key not in _TRANSLATIONS[lang])
        assert missing == [], f"untranslated in {lang}: {missing}"


def test_templates_have_no_inline_page_scripts():
    """Only data islands and the tiny bootstrap snippets stay inline."""
    allowed_inline = {"base.html", "job_panel.html"}  # pre-paint theme; HTMX-swapped fragment
    for template in TEMPLATES.glob("*.html"):
        if template.name in allowed_inline:
            continue
        html = template.read_text(encoding="utf-8")
        inline = re.findall(r"<script(?![^>]*\b(?:src=|type=\"application/json\"))[^>]*>", html)
        assert inline == [], f"{template.name} has inline <script>; move it to app/static/js"
        assert "<style" not in html, f"{template.name} has inline <style>; move it to app/static/css"


def test_geo_maps_use_the_vendored_map_outlines():
    """Plotly fetches map outlines from cdn.plot.ly by default, which the CSP (connect-src 'self') blocks."""
    spec = importlib.util.spec_from_file_location("fetch_vendor_assets", ROOT / "scripts" / "fetch_vendor_assets.py")
    assert spec is not None
    assert spec.loader is not None
    fetch_vendor_assets = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fetch_vendor_assets)

    for script in STATIC_JS.glob("*.js"):
        source = script.read_text(encoding="utf-8")
        if "scattergeo" in source:
            assert 'topojsonURL: "/static/vendor/plotly/topojson/"' in source, script.name
    assert "plotly/topojson/world_110m.json" in fetch_vendor_assets.FILES
