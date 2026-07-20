"""
fetch_vendor_assets.py — populate app/static/vendor/ with self-hosted copies
of the third-party assets base.html and the map pages currently load from
CDNs (unpkg.com, cdn.plot.ly, fonts.googleapis.com).

Run this once from a machine that has outbound internet access, then commit
the downloaded files (or bake them into the Docker image) — ConverterHub is a
local-network tool and shouldn't depend on internet access at request time.

Usage:
    python scripts/fetch_vendor_assets.py
"""
from __future__ import annotations

import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "app" / "static" / "vendor"

# A real browser UA is required — fonts.googleapis.com serves .ttf to unknown
# clients and only returns modern woff2 (much smaller) to recognised browsers.
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

FILES = {
    "htmx/htmx.min.js": "https://unpkg.com/htmx.org@1.9.12/dist/htmx.min.js",
    "htmx/htmx-sse.js": "https://unpkg.com/htmx-ext-sse@2.2.1/sse.js",
    "plotly/plotly.min.js": "https://cdn.plot.ly/plotly-2.35.2.min.js",
}

GOOGLE_FONTS_CSS_URL = (
    "https://fonts.googleapis.com/css2"
    "?family=Cormorant+Garamond:ital,wght@0,400;0,500;0,600;1,400;1,500"
    "&family=DM+Sans:wght@300;400;500"
    "&family=JetBrains+Mono:wght@300;400"
    "&display=swap"
)


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def fetch_plain_files() -> None:
    for rel_path, url in FILES.items():
        dest = VENDOR / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        print(f"Fetching {url} -> {dest.relative_to(ROOT)}")
        dest.write_bytes(_fetch(url))


def fetch_fonts() -> None:
    """
    Google Fonts serves a CSS file whose @font-face rules point at
    fonts.gstatic.com URLs that change over time (they're content-hashed).
    We download the CSS, pull down every referenced font file, and rewrite
    the CSS to point at the local copies so no request leaves the network.
    """
    print(f"Fetching {GOOGLE_FONTS_CSS_URL}")
    css = _fetch(GOOGLE_FONTS_CSS_URL).decode("utf-8")

    fonts_dir = VENDOR / "fonts"
    files_dir = fonts_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    urls = sorted(set(re.findall(r"url\((https://fonts\.gstatic\.com/[^)]+)\)", css)))
    for font_url in urls:
        filename = font_url.rsplit("/", 1)[-1]
        dest = files_dir / filename
        print(f"  Fetching font file {filename}")
        dest.write_bytes(_fetch(font_url))
        css = css.replace(font_url, f"files/{filename}")

    (fonts_dir / "fonts.css").write_text(css, encoding="utf-8")
    print(f"Wrote {fonts_dir / 'fonts.css'} referencing {len(urls)} local font file(s)")


def main() -> None:
    fetch_plain_files()
    fetch_fonts()
    print("\nDone. Restart the app (or just reload — StaticFiles serves these live).")


if __name__ == "__main__":
    main()
