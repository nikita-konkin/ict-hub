"""
fetch_vendor_assets.py — populate app/static/vendor/ with self-hosted copies
of the third-party assets the pages load (htmx, Plotly, the web fonts).

ConverterHub is a local-network tool and shouldn't depend on internet access
at request time, so the assets are served by the app itself. They are not
committed: the Docker build runs this script (see the Dockerfile). Run it by
hand only when serving the app outside Docker:

    python scripts/fetch_vendor_assets.py

Scripts are pinned by SHA-256 and the run fails if a download differs. Google
Fonts serves unversioned CSS, so the fonts are checked for shape instead
(only fonts.gstatic.com URLs, every file a WOFF2 font).
"""

from __future__ import annotations

import hashlib
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "app" / "static" / "vendor"

# A real browser UA is required — fonts.googleapis.com serves .ttf to unknown
# clients and only returns modern woff2 (much smaller) to recognised browsers.
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

# path under vendor/ -> (url, sha256). To upgrade, change the URL and put the
# new file's hash here (e.g. compare the CDN copy with the npm package on
# cdn.jsdelivr.net, as was done for these).
FILES = {
    "htmx/htmx.min.js": (
        "https://unpkg.com/htmx.org@1.9.12/dist/htmx.min.js",
        "449317ade7881e949510db614991e195c3a099c4c791c24dacec55f9f4a2a452",
    ),
    "plotly/plotly.min.js": (
        "https://cdn.plot.ly/plotly-2.35.2.min.js",
        "6d21266ce1bd7d9e5ab4e115989c70c20de0382fd973a8f26ab58619eba4d603",
    ),
    # Country and coastline outlines for Plotly geo maps (the stations map).
    # Plotly would fetch it from cdn.plot.ly in the browser, which the CSP
    # (connect-src 'self') forbids; stations-map.js points topojsonURL here.
    # Same bytes as dist/topojson/world_110m.json in the plotly.js 2.35.2 package.
    "plotly/topojson/world_110m.json": (
        "https://cdn.plot.ly/world_110m.json",
        "d75915eaa31c870df6b972c9e5bb86910197825f33dcfef740f3b2f68cffe843",
    ),
}

GOOGLE_FONTS_CSS_URL = (
    "https://fonts.googleapis.com/css2"
    "?family=Cormorant+Garamond:ital,wght@0,400;0,500;0,600;1,400;1,500"
    "&family=DM+Sans:wght@300;400;500"
    "&family=JetBrains+Mono:wght@300;400"
    "&display=swap"
)

_ATTEMPTS = 3


class FetchError(RuntimeError):
    """A download failed or did not look like what was expected."""


def _fetch(url: str) -> bytes:
    """GET url, retrying transient network errors."""
    for attempt in range(1, _ATTEMPTS + 1):
        # Only called with the fixed https URLs in this script and the
        # fonts.gstatic.com URLs the Google Fonts CSS points at.
        req = urllib.request.Request(url, headers={"User-Agent": UA})  # noqa: S310
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
                return resp.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == _ATTEMPTS:
                raise FetchError(f"{url}: {exc}") from exc
            print(f"  {url}: {exc}; retrying")
            time.sleep(2 * attempt)
    raise AssertionError("unreachable")


def fetch_plain_files() -> None:
    for rel_path, (url, expected_sha256) in FILES.items():
        dest = VENDOR / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        print(f"Fetching {url} -> {dest.relative_to(ROOT)}")
        data = _fetch(url)
        actual_sha256 = hashlib.sha256(data).hexdigest()
        if actual_sha256 != expected_sha256:
            raise FetchError(f"{url}: sha256 {actual_sha256} does not match the pinned {expected_sha256}")
        dest.write_bytes(data)


def fetch_fonts() -> None:
    """
    Google Fonts serves a CSS file whose @font-face rules point at
    fonts.gstatic.com URLs that change over time (they're content-hashed).
    We download the CSS, pull down every referenced font file, and rewrite
    the CSS to point at the local copies so no request leaves the network.
    """
    print(f"Fetching {GOOGLE_FONTS_CSS_URL}")
    css = _fetch(GOOGLE_FONTS_CSS_URL).decode("utf-8")

    urls = sorted(set(re.findall(r"url\(([^)]+)\)", css)))
    if not urls:
        raise FetchError("the Google Fonts CSS references no font files")
    foreign = [url for url in urls if not url.startswith("https://fonts.gstatic.com/")]
    if foreign:
        raise FetchError(f"the Google Fonts CSS references unexpected URLs: {foreign}")

    fonts_dir = VENDOR / "fonts"
    files_dir = fonts_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    for font_url in urls:
        filename = font_url.rsplit("/", 1)[-1]
        print(f"  Fetching font file {filename}")
        data = _fetch(font_url)
        if not data.startswith(b"wOF2"):
            raise FetchError(f"{font_url} is not a WOFF2 font")
        (files_dir / filename).write_bytes(data)
        css = css.replace(font_url, f"files/{filename}")

    (fonts_dir / "fonts.css").write_text(css, encoding="utf-8")
    print(f"Wrote {fonts_dir / 'fonts.css'} referencing {len(urls)} local font file(s)")


def main() -> int:
    try:
        fetch_plain_files()
        fetch_fonts()
    except FetchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print("\nDone. Restart the app (or just reload — StaticFiles serves these live).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
