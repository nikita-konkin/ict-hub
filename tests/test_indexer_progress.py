"""Tests for the data-indexer progress banner."""

import pytest


def _scans(monkeypatch, scans):
    async def stub():
        return scans

    monkeypatch.setattr("app.indexer_progress.get_indexer_scans_async", stub)


def _scan(**fields):
    return {"type": "rinex", "root": "/mnt/rinex-zqo", "started_at": 1.0, "elapsed_seconds": 83.0, **fields}


def test_banner_names_the_data_being_indexed(operator_client, monkeypatch):
    _scans(monkeypatch, [_scan(type="abstec", expected_seconds=245.0)])
    assert "AbsTEC" in operator_client.get("/indexer/progress").text, "Banner did not name the data being indexed"


def test_banner_shows_elapsed_and_expected_time(operator_client, monkeypatch):
    _scans(monkeypatch, [_scan(expected_seconds=245.0)])
    assert "1 min 23 s of about 4 min 05 s" in operator_client.get("/indexer/progress").text, (
        "Banner did not show how long the scan has run and is expected to run"
    )


def test_banner_fills_the_bar_by_elapsed_share_of_expected(operator_client, monkeypatch):
    _scans(monkeypatch, [_scan(elapsed_seconds=60.0, expected_seconds=240.0)])
    assert 'aria-valuenow="25"' in operator_client.get("/indexer/progress").text, (
        "Bar did not reflect the share of time elapsed"
    )


def test_banner_admits_an_overrunning_scan(operator_client, monkeypatch):
    _scans(monkeypatch, [_scan(elapsed_seconds=400.0, expected_seconds=245.0)])
    assert "longer than last time" in operator_client.get("/indexer/progress").text, (
        "Banner did not admit the scan is overrunning its estimate"
    )


def test_banner_says_when_there_is_no_estimate(operator_client, monkeypatch):
    _scans(monkeypatch, [_scan(expected_seconds=None)])
    assert "no estimate yet" in operator_client.get("/indexer/progress").text, (
        "Banner did not say a first scan has no estimate"
    )


@pytest.mark.parametrize("scans", [[], [_scan(elapsed_seconds=2.0, expected_seconds=3.0)]])
def test_banner_stays_hidden_without_long_scans(operator_client, monkeypatch, scans):
    _scans(monkeypatch, scans)
    assert "indexer-progress-card" not in operator_client.get("/indexer/progress").text, (
        "Banner appeared although no long scan was running"
    )


def test_banner_speaks_russian(operator_client, monkeypatch):
    _scans(monkeypatch, [_scan(expected_seconds=245.0)])
    assert "1 мин 23 с из примерно 4 мин 05 с" in operator_client.get("/indexer/progress?lang=ru").text, (
        "Banner did not translate the timing into Russian"
    )


def test_layout_polls_the_banner_for_signed_in_users(operator_client, monkeypatch):
    _scans(monkeypatch, [])
    assert 'hx-get="/indexer/progress"' in operator_client.get("/history").text, "Layout did not poll indexer progress"


def test_banner_requires_sign_in(client):
    assert client.get("/indexer/progress", follow_redirects=False).status_code in (303, 401), (
        "Banner was served to an anonymous visitor"
    )
