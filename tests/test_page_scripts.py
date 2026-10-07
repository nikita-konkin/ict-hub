"""
test_page_scripts.py — Page scripts in app/static/js and their data islands.

Page scripts are static files, so translated strings reach them through a JSON
data island rendered with t_dict() right before the <script src> tag. A key a
script looks up with T("...") but its island does not list shows up on the
page as the raw key; these tests catch that without a browser.
"""

import importlib.util
import re
from decimal import Decimal, InvalidOperation
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


def test_number_inputs_accept_their_default_value():
    """Browsers refuse to submit a form whose number input misses its step, so a bad default blocks the form."""
    checked = 0
    for template in TEMPLATES.glob("*.html"):
        for tag in re.findall(r"<input\b[^>]*>", template.read_text(encoding="utf-8")):
            attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', tag))
            if attrs.get("type") != "number" or "{{" in tag:
                continue
            try:
                value = Decimal(attrs["value"])
                low = Decimal(attrs["min"])
            except (KeyError, InvalidOperation):
                continue  # no default, or no min: the step then counts from the default itself
            assert value >= low, f"{template.name}: {tag}"
            if "max" in attrs:
                assert value <= Decimal(attrs["max"]), f"{template.name}: {tag}"
            step = attrs.get("step", "1")
            if step != "any":
                steps = (value - low) / Decimal(step)
                assert steps == steps.to_integral_value(), f"{template.name}: {tag}"
            checked += 1
    assert checked, "no number inputs with a default and a min found"


def _vendor_files() -> dict[str, tuple[str, str]]:
    """FILES from scripts/fetch_vendor_assets.py: path under vendor/ -> (url, sha256)."""
    spec = importlib.util.spec_from_file_location("fetch_vendor_assets", ROOT / "scripts" / "fetch_vendor_assets.py")
    assert spec is not None
    assert spec.loader is not None
    fetch_vendor_assets = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fetch_vendor_assets)
    return fetch_vendor_assets.FILES


def test_geo_maps_use_the_vendored_map_outlines():
    """Plotly fetches map outlines from cdn.plot.ly by default, which the CSP (connect-src 'self') blocks."""
    for script in STATIC_JS.glob("*.js"):
        source = script.read_text(encoding="utf-8")
        if "scattergeo" in source:
            assert 'topojsonURL: "/static/vendor/plotly/topojson/"' in source, script.name
    assert "plotly/topojson/world_110m.json" in _vendor_files()


def test_osm_maps_draw_labels_from_vendored_glyphs():
    """Plotly's map styles load label glyphs from fonts.openmaptiles.org, which the CSP blocks."""
    files = _vendor_files()
    scripts = [script for script in STATIC_JS.glob("*.js") if "scattermap" in script.read_text(encoding="utf-8")]
    assert scripts, "no script draws a scattermap"
    for script in scripts:
        source = script.read_text(encoding="utf-8")
        assert "/static/vendor/maplibre/glyphs/{fontstack}/{range}.pbf" in source, script.name
        font = re.search(r'const LABEL_FONT = "([^"]+)";', source)
        assert font, f"{script.name} names no LABEL_FONT"
        for glyph_range in ("0-255", "1024-1279"):  # Latin, Cyrillic
            assert f"maplibre/glyphs/{font.group(1)}/{glyph_range}.pbf" in files, script.name


def test_page_scripts_are_revalidated_after_a_deploy(client):
    """Without Cache-Control, browsers kept running the previous release's scripts."""
    r = client.get("/static/js/stations-map.js")
    assert r.status_code == 200
    assert r.headers["Cache-Control"] == "no-cache"

    unchanged = client.get("/static/js/stations-map.js", headers={"If-None-Match": r.headers["ETag"]})
    assert unchanged.status_code == 304
    assert unchanged.headers["Cache-Control"] == "no-cache"
