"""
test_security_hardening.py — tests for the security/accounting hardening:
SECRET_KEY resolution, weak-admin detection, forced password rotation, the
self-service password change, login rate-limiting, the Origin/CSRF guard,
response security headers, and the audit trail.
"""
import pytest

from app import auth as auth_mod
from app import config as cfg
from app.auth import hash_password
from app.models import AuditLog, User


@pytest.fixture(autouse=True)
def _clear_login_limiter():
    """Isolate the process-global login-attempt counter for each test here."""
    with auth_mod._login_attempts_lock:
        auth_mod._login_attempts.clear()
    yield
    with auth_mod._login_attempts_lock:
        auth_mod._login_attempts.clear()


# ── SECRET_KEY resolution ─────────────────────────────────────────────────────

def test_is_weak_admin_password():
    assert cfg.is_weak_admin_password("admin")
    assert cfg.is_weak_admin_password("short")          # < 8 chars
    assert cfg.is_weak_admin_password("")
    assert not cfg.is_weak_admin_password("a-strong-enough-passphrase")


def test_resolve_secret_key_fail_fast_when_required(monkeypatch):
    monkeypatch.setattr(cfg, "REQUIRE_STRONG_SECRET", True)
    monkeypatch.setenv("SECRET_KEY", "change-me-in-production-please-32chars!!")
    with pytest.raises(RuntimeError):
        cfg._resolve_secret_key()


def test_resolve_secret_key_ephemeral_in_dev(monkeypatch):
    monkeypatch.setattr(cfg, "REQUIRE_STRONG_SECRET", False)
    monkeypatch.setenv("SECRET_KEY", "")
    key = cfg._resolve_secret_key()
    assert len(key) >= 32
    assert key not in cfg.INSECURE_SECRET_KEYS


def test_resolve_secret_key_accepts_strong(monkeypatch):
    monkeypatch.setattr(cfg, "REQUIRE_STRONG_SECRET", True)
    strong = "0123456789abcdef" * 4  # 64 chars, not a placeholder
    monkeypatch.setenv("SECRET_KEY", strong)
    assert cfg._resolve_secret_key() == strong


# ── Forced password rotation + self-service change ────────────────────────────

def test_forced_password_change_flow(client, db):
    u = User(username="mustchange", hashed_pw=hash_password("oldpass123"),
             role="operator", is_active=True, must_change_password=True)
    db.add(u)
    db.commit()

    login = client.post("/login", data={"username": "mustchange", "password": "oldpass123"},
                        follow_redirects=False)
    assert login.status_code in (302, 303)
    assert login.headers.get("location", "").endswith("/account/password")

    # Any other protected page funnels back to the change-password page.
    blocked = client.get("/analysis", follow_redirects=False)
    assert blocked.status_code in (302, 303)
    assert "/account/password" in blocked.headers.get("location", "")

    changed = client.post("/account/password", data={
        "current_password": "oldpass123",
        "new_password": "brandnew123",
        "confirm_password": "brandnew123",
    }, follow_redirects=False)
    assert changed.status_code == 200

    db.refresh(u)
    assert u.must_change_password is False

    # Now the app is usable again.
    assert client.get("/analysis", follow_redirects=True).status_code == 200


def test_password_change_wrong_current(operator_client):
    r = operator_client.post("/account/password", data={
        "current_password": "nope", "new_password": "brandnew123", "confirm_password": "brandnew123",
    })
    assert r.status_code == 400


def test_password_change_mismatch(operator_client):
    r = operator_client.post("/account/password", data={
        "current_password": "operpass", "new_password": "brandnew123", "confirm_password": "different123",
    })
    assert r.status_code == 400


def test_password_change_too_short(operator_client):
    r = operator_client.post("/account/password", data={
        "current_password": "operpass", "new_password": "short", "confirm_password": "short",
    })
    assert r.status_code == 400


def test_password_change_success_clears_and_persists(operator_client, db):
    r = operator_client.post("/account/password", data={
        "current_password": "operpass", "new_password": "operpass-new-9", "confirm_password": "operpass-new-9",
    })
    assert r.status_code == 200
    user = db.query(User).filter(User.username == "test_operator").first()
    from app.auth import verify_password
    assert verify_password("operpass-new-9", user.hashed_pw)


# ── Login rate limiting ───────────────────────────────────────────────────────

def test_login_lockout_after_threshold(client, admin_user, monkeypatch):
    monkeypatch.setattr(cfg, "LOGIN_RATE_LIMIT_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(cfg, "LOGIN_RATE_LIMIT_ENABLED", True)

    for _ in range(3):
        r = client.post("/login", data={"username": "test_admin", "password": "wrong"},
                        follow_redirects=False)
        assert r.status_code == 401

    # Further attempts are locked out — even with the CORRECT password.
    locked = client.post("/login", data={"username": "test_admin", "password": "adminpass"},
                        follow_redirects=False)
    assert locked.status_code == 429


def test_login_success_resets_counter(client, admin_user, monkeypatch):
    monkeypatch.setattr(cfg, "LOGIN_RATE_LIMIT_MAX_ATTEMPTS", 3)
    # Two failures, then a success clears the counter.
    for _ in range(2):
        client.post("/login", data={"username": "test_admin", "password": "wrong"}, follow_redirects=False)
    ok = client.post("/login", data={"username": "test_admin", "password": "adminpass"}, follow_redirects=False)
    assert ok.status_code in (302, 303)
    assert not auth_mod.login_is_locked("testclient", "test_admin")


# ── CSRF / Origin guard ───────────────────────────────────────────────────────

def test_cross_origin_post_blocked(client, admin_user):
    r = client.post("/login", data={"username": "test_admin", "password": "adminpass"},
                    headers={"origin": "http://evil.example"}, follow_redirects=False)
    assert r.status_code == 403


def test_same_origin_post_allowed(client, admin_user):
    r = client.post("/login", data={"username": "test_admin", "password": "adminpass"},
                    headers={"origin": "http://testserver"}, follow_redirects=False)
    assert r.status_code in (302, 303)


def test_get_not_blocked_by_csrf(client):
    assert client.get("/login", follow_redirects=False).status_code == 200


# ── Security headers ──────────────────────────────────────────────────────────

def test_security_headers_present(client):
    r = client.get("/login")
    assert r.headers.get("X-Frame-Options") == "DENY"
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert "Content-Security-Policy" in r.headers
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]


# ── Audit trail ───────────────────────────────────────────────────────────────

def test_login_success_is_audited(client, admin_user, db):
    client.post("/login", data={"username": "test_admin", "password": "adminpass"}, follow_redirects=False)
    rows = db.query(AuditLog).filter(AuditLog.action == "login.success").all()
    assert any(r.actor_username == "test_admin" for r in rows)


def test_failed_login_is_audited(client, admin_user, db):
    client.post("/login", data={"username": "test_admin", "password": "wrong"}, follow_redirects=False)
    rows = db.query(AuditLog).filter(AuditLog.action == "login.failed").all()
    assert any(r.actor_username == "test_admin" for r in rows)


def test_audit_view_admin_only(operator_client):
    assert operator_client.get("/audit", follow_redirects=False).status_code == 403


def test_audit_view_renders_for_admin(admin_client):
    assert admin_client.get("/audit", follow_redirects=True).status_code == 200
