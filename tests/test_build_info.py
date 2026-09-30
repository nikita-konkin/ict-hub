"""Tests for the build version and time shown on the dashboard."""

from datetime import UTC, datetime

from app import build_info
from app.build_info import BuildInfo, read_build_info

SHA = "4d12ad6f0c3b1e2a9d8c7b6a5f4e3d2c1b0a9f8e"


def test_reads_version_and_build_time(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_VERSION", SHA)
    build_time_file = tmp_path / "BUILD_TIME"
    build_time_file.write_text("2026-09-30T20:10:05Z\n", encoding="utf-8")

    info = read_build_info(build_time_file)

    assert info.version == SHA
    assert info.version_short == "4d12ad6"
    assert info.built_at == datetime(2026, 9, 30, 20, 10, 5, tzinfo=UTC)


def test_other_versions_are_shown_as_given(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_VERSION", "v1.4.0")
    assert read_build_info(tmp_path / "missing").version_short == "v1.4.0"


def test_outside_docker(tmp_path, monkeypatch):
    monkeypatch.delenv("APP_VERSION", raising=False)
    bad_file = tmp_path / "BUILD_TIME"
    bad_file.write_text("not a date", encoding="utf-8")

    for path in (tmp_path / "missing", bad_file):
        info = read_build_info(path)
        assert info.version == "dev"
        assert info.built_at is None


def test_dashboard_shows_version_and_build_time(operator_client, monkeypatch):
    built_at = datetime(2026, 9, 30, 20, 10, tzinfo=UTC)
    monkeypatch.setattr(build_info, "BUILD_INFO", BuildInfo(SHA, "4d12ad6", built_at))

    html = operator_client.get("/").text

    assert f'title="{SHA}">4d12ad6</span>' in html
    assert 'data-local-datetime="2026-09-30T20:10:00+00:00" data-local-datetime-year>30 Sep 2026, 20:10 UTC' in html


def test_dashboard_without_build_time(operator_client, monkeypatch):
    monkeypatch.setattr(build_info, "BUILD_INFO", BuildInfo("dev", "dev", None))

    html = operator_client.get("/").text

    assert 'title="dev">dev</span>' in html
    assert "data-local-datetime-year" not in html
