"""
test_layout.py — Responsive layout guards for the shared page shell.

Below 900px the sidebar is off-canvas behind a menu button, and the language
and theme controls share one fixed row; plain 1fr grid tracks cannot shrink
below their content, so templates use the minmax-based classes in app.css.
"""

import re
from pathlib import Path

from fastapi.testclient import TestClient

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"


def test_shell_renders_menu_button_and_control_row(admin_client: TestClient):
    html = admin_client.get("/").text
    assert 'id="nav-toggle"' in html
    assert 'aria-controls="sidebar"' in html
    assert 'id="sidebar"' in html
    assert "data-nav-close" in html
    row = html.index('class="top-controls"')
    assert row < html.index('id="lang-toggle"') < html.index('id="theme-toggle"')


def test_templates_have_no_unshrinkable_grid_tracks():
    """A bare 1fr track keeps its content's min width and overflows on phones."""
    bare = re.compile(r"grid-template-columns:\s*(?:[^;\"]*\s)?1fr\b")
    offenders = [
        f"{template.name}: {match.group(0)}"
        for template in TEMPLATES.glob("*.html")
        for match in bare.finditer(template.read_text(encoding="utf-8"))
    ]
    assert offenders == []
