"""Tests for the translation table in app/i18n.py."""

import re
from pathlib import Path

from app.i18n import _TRANSLATIONS

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "app" / "templates"

# t('key') with a literal key; t('prefix_' ~ name) is dynamic and not matched.
_LITERAL_KEY_RE = re.compile(r"""\bt\(\s*['"]([A-Za-z0-9_]+)['"]\s*\)""")


def _template_keys() -> set[str]:
    keys: set[str] = set()
    for path in TEMPLATES_DIR.glob("*.html"):
        keys |= set(_LITERAL_KEY_RE.findall(path.read_text(encoding="utf-8")))
    return keys


def test_every_template_key_is_translated_in_every_language():
    keys = _template_keys()
    assert keys, "no t('...') calls found; is TEMPLATES_DIR right?"
    for lang, table in _TRANSLATIONS.items():
        assert sorted(keys - table.keys()) == [], f"missing {lang} translations"


def test_languages_define_the_same_keys():
    en = _TRANSLATIONS["en"].keys()
    for lang, table in _TRANSLATIONS.items():
        assert sorted(en ^ table.keys()) == [], f"{lang} keys differ from en"
