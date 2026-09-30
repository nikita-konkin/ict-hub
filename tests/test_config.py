"""Tests for SECRET_KEY resolution in app/config.py."""

import os
import stat

import pytest

from app import config

PLACEHOLDER = "replace-me-with-a-random-32-char-string!!"


@pytest.fixture
def key_file(tmp_path, monkeypatch):
    path = tmp_path / "data" / ".secret_key"
    monkeypatch.setenv("SECRET_KEY_FILE", str(path))
    return path


def test_explicit_secret_key_is_used_as_is(monkeypatch, key_file):
    monkeypatch.setenv("SECRET_KEY", "a-real-deployment-key")
    assert config._resolve_secret_key() == "a-real-deployment-key"
    assert not key_file.exists()


@pytest.mark.parametrize("value", ["", PLACEHOLDER, "change-me-in-production-please-32chars!!"])
def test_placeholder_key_is_replaced_by_generated_persisted_key(monkeypatch, key_file, value):
    monkeypatch.setenv("SECRET_KEY", value)

    first = config._resolve_secret_key()

    assert first not in config._PLACEHOLDER_SECRET_KEYS
    assert len(first) >= 32
    assert key_file.read_text(encoding="utf-8") == first
    # A restart must reuse the stored key, or every session would be dropped.
    assert config._resolve_secret_key() == first


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_generated_key_file_is_private(monkeypatch, key_file):
    monkeypatch.setenv("SECRET_KEY", PLACEHOLDER)
    config._resolve_secret_key()
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600


def test_default_key_file_sits_next_to_sqlite_database(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{tmp_path / 'hub.db'}")
    assert config._default_secret_key_file() == os.path.join(str(tmp_path), ".secret_key")


def test_in_memory_database_gets_an_ephemeral_key(monkeypatch):
    monkeypatch.delenv("SECRET_KEY_FILE", raising=False)
    monkeypatch.setenv("SECRET_KEY", PLACEHOLDER)
    monkeypatch.setattr(config, "DATABASE_URL", "sqlite:///:memory:")

    first = config._resolve_secret_key()
    second = config._resolve_secret_key()

    assert first not in config._PLACEHOLDER_SECRET_KEYS
    assert first != second  # nothing persisted
