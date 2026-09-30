"""
build_info.py — version and build time of the running image, shown on the
dashboard.

The Docker build records both (see the Dockerfile): APP_VERSION is a build
argument that CI sets to the commit SHA the image is tagged with, and the
build writes its own time to BUILD_TIME next to the app package. Outside
Docker the version is "dev" and the build time is unknown.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

BUILD_TIME_FILE = Path(__file__).resolve().parent.parent / "BUILD_TIME"


@dataclass(frozen=True)
class BuildInfo:
    version: str  # as given, e.g. a full commit SHA
    version_short: str  # what the page shows: a SHA cut to 7 characters
    built_at: datetime | None  # UTC


def read_build_info(build_time_file: Path = BUILD_TIME_FILE) -> BuildInfo:
    version = os.getenv("APP_VERSION", "").strip() or "dev"
    version_short = version[:7] if re.fullmatch(r"[0-9a-f]{40}", version) else version
    try:
        built_at: datetime | None = datetime.fromisoformat(build_time_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        built_at = None
    if built_at is not None and built_at.tzinfo is None:
        built_at = built_at.replace(tzinfo=UTC)
    return BuildInfo(version, version_short, built_at)


BUILD_INFO = read_build_info()
