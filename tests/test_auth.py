"""
test_auth.py — Tests for the authentication system.

We test the full request lifecycle including cookies and session state,
not just the route functions in isolation. This gives us confidence that
the SessionMiddleware, password hashing, and redirect logic all work together.
"""
import pytest
from fastapi.testclient import TestClient


class TestLoginForm:
    """Tests for GET /login — the login page itself."""

    def test_renders_login_page(self, client):
        """Unauthenticated users should see the login form."""
        response = client.get("/login", follow_redirects=False)
        assert response.status_code == 200
        assert b"Sign in" in response.content

    def test_already_logged_in_redirects_to_dashboard(self, admin_client):
        """Visiting /login while authenticated should bounce to the dashboard."""
        response = admin_client.get("/login", follow_redirects=False)
        # The redirect destination is / (dashboard)
        assert response.status_code in (302, 303, 307)


class TestLoginSubmit:
    """Tests for POST /login — credential validation."""

    def test_successful_login_redirects_to_dashboard(self, client, admin_user):
        """Correct credentials should set a session and redirect to /."""
        response = client.post(
            "/login",
            data={"username": "test_admin", "password": "adminpass"},
            follow_redirects=False,
        )
        # FastAPI redirects after successful login
        assert response.status_code in (302, 303)
        assert response.headers.get("location") in ("/", "http://testserver/")

    def test_wrong_password_returns_error(self, client, admin_user):
        """A wrong password should render the login form with an error message."""
        response = client.post(
            "/login",
            data={"username": "test_admin", "password": "wrongpassword"},
            follow_redirects=True,
        )
        assert response.status_code in (200, 401)
        assert b"Invalid" in response.content

    def test_nonexistent_user_returns_error(self, client):
        """A username that doesn't exist should fail gracefully (no 500)."""
        response = client.post(
            "/login",
            data={"username": "ghost", "password": "anything"},
            follow_redirects=True,
        )
        assert response.status_code in (200, 401)
        assert b"Invalid" in response.content

    def test_inactive_user_cannot_login(self, client, inactive_user):
        """Deactivated accounts should be refused even with the correct password."""
        response = client.post(
            "/login",
            data={"username": "test_inactive", "password": "somepass"},
            follow_redirects=True,
        )
        assert response.status_code in (200, 403)
        # The response should mention account status
        assert b"deactivated" in response.content.lower()


class TestLogout:
    """Tests for GET /logout."""

    def test_logout_clears_session_and_redirects(self, admin_client):
        """After logout the user should be sent to /login."""
        response = admin_client.get("/logout", follow_redirects=False)
        assert response.status_code in (302, 303)
        location = response.headers.get("location", "")
        assert "login" in location

    def test_after_logout_cannot_access_protected_routes(self, admin_client):
        """Logging out should invalidate the session so protected pages redirect."""
        # First log out
        admin_client.get("/logout", follow_redirects=True)
        # Now try to hit the dashboard
        response = admin_client.get("/", follow_redirects=False)
        # Should be redirected to login, not served the dashboard
        assert response.status_code in (302, 303)


class TestProtectedRoutes:
    """Tests that authentication gates work correctly on protected endpoints."""

    def test_unauthenticated_dashboard_redirects_to_login(self, client):
        response = client.get("/", follow_redirects=False)
        assert response.status_code in (302, 303)

    def test_unauthenticated_history_redirects_to_login(self, client):
        response = client.get("/history", follow_redirects=False)
        assert response.status_code in (302, 303)

    def test_authenticated_can_access_dashboard(self, operator_client):
        response = operator_client.get("/", follow_redirects=True)
        assert response.status_code == 200

    def test_authenticated_can_access_history(self, operator_client):
        response = operator_client.get("/history", follow_redirects=True)
        assert response.status_code == 200


class TestUserManagement:
    """Tests for /users — admin-only user management."""

    def test_admin_can_access_users_page(self, admin_client):
        response = admin_client.get("/users", follow_redirects=True)
        assert response.status_code == 200

    def test_operator_cannot_access_users_page(self, operator_client):
        """Non-admins should get a 403, not a 200."""
        response = operator_client.get("/users", follow_redirects=False)
        assert response.status_code == 403

    def test_admin_can_create_user(self, admin_client, db):
        response = admin_client.post(
            "/users",
            data={"username": "newuser", "password": "newpass123", "role": "operator"},
            follow_redirects=True,
        )
        assert response.status_code == 200

        # Verify the user was actually created in the database
        from app.models import User
        user = db.query(User).filter(User.username == "newuser").first()
        assert user is not None
        assert user.role == "operator"

    def test_duplicate_username_returns_error(self, admin_client, admin_user):
        """Attempting to create a user with an existing username should fail gracefully."""
        response = admin_client.post(
            "/users",
            data={"username": "test_admin", "password": "anypass", "role": "operator"},
            follow_redirects=True,
        )
        # Should re-render the form with an error, not crash
        assert response.status_code in (200, 400)
        assert b"taken" in response.content.lower() or b"already" in response.content.lower()

    def test_admin_can_toggle_user_active_state(self, admin_client, operator_user, db):
        response = admin_client.post(
            f"/users/{operator_user.id}/toggle",
            follow_redirects=True,
        )
        assert response.status_code == 200

        # Verify the database was updated
        db.refresh(operator_user)
        assert operator_user.is_active is False

    def test_admin_cannot_deactivate_themselves(self, admin_client, admin_user):
        """An admin deactivating their own account is prevented server-side."""
        response = admin_client.post(
            f"/users/{admin_user.id}/toggle",
            follow_redirects=False,
        )
        assert response.status_code == 400
