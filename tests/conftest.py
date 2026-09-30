"""
conftest.py — Shared pytest fixtures.

Key design decisions:
  - We use an in-memory SQLite database for tests so each test run starts clean
    and nothing is written to disk. SQLAlchemy receives a fresh engine per
    test session and all tables are created before any test runs.
  - Tests never reach a Docker daemon: docker.from_env() fails as if Docker
    were unavailable (see no_real_docker), and tests that need container
    behaviour patch the runner functions or docker.from_env themselves.
  - We use FastAPI's TestClient (backed by httpx) which runs the full ASGI
    middleware stack, including session middleware and auth dependencies.
"""

import os

import docker
import docker.errors
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Set test database BEFORE importing app modules
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["JOB_RUNTIME_ENABLED"] = "0"

from datetime import UTC

from app.auth import hash_password
from app.database import Base, get_db
from app.main import app
from app.models import JobRun, User

# ─────────────────────────────────────────────────────────────────────────────
# In-memory database for tests
# ─────────────────────────────────────────────────────────────────────────────

TEST_DB_URL = "sqlite:///:memory:"

test_engine = create_engine(
    TEST_DB_URL,
    connect_args={"check_same_thread": False},
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(autouse=True)
def no_real_docker(monkeypatch):
    """Keep tests away from the Docker daemon of the machine running them.

    Without this, a test that forgets to patch start_container launches a real
    container, and page renders list the host's real containers.
    """

    def _refuse(*args, **kwargs):
        raise docker.errors.DockerException("tests must not use the real Docker daemon")

    monkeypatch.setattr(docker, "from_env", _refuse)


@pytest.fixture(scope="session", autouse=True)
def create_tables():
    """Create all tables once for the entire test session."""
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def db():
    """
    Provide a clean database session for each test and roll back all changes
    afterwards. This isolation prevents one test from affecting another.
    """
    connection = test_engine.connect()
    transaction = connection.begin()
    session = TestSessionLocal(bind=connection)

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def client(db):
    """
    FastAPI TestClient with the database dependency overridden to use our
    test session. This is FastAPI's recommended approach to dependency
    injection in tests.
    """

    def override_get_db():
        try:
            yield db
        finally:
            pass  # rollback happens in the db fixture

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()


# ─────────────────────────────────────────────────────────────────────────────
# User fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def admin_user(db) -> User:
    """Create and persist an admin user for use in tests."""
    user = User(
        username="test_admin",
        hashed_pw=hash_password("adminpass"),
        role="admin",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def operator_user(db) -> User:
    """Create and persist an operator user for use in tests."""
    user = User(
        username="test_operator",
        hashed_pw=hash_password("operpass"),
        role="operator",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def inactive_user(db) -> User:
    """Create an inactive (deactivated) user."""
    user = User(
        username="test_inactive",
        hashed_pw=hash_password("somepass"),
        role="operator",
        is_active=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# ─────────────────────────────────────────────────────────────────────────────
# Authenticated client helpers
# ─────────────────────────────────────────────────────────────────────────────


def _login(client: TestClient, username: str, password: str) -> TestClient:
    """Helper that logs in via the form endpoint and returns the client."""
    response = client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=True,
    )
    assert response.status_code == 200, f"Login failed: {response.text}"
    return client


@pytest.fixture
def admin_client(client, admin_user) -> TestClient:
    """TestClient with an active admin session."""
    return _login(client, "test_admin", "adminpass")


@pytest.fixture
def operator_client(client, operator_user) -> TestClient:
    """TestClient with an active operator session."""
    return _login(client, "test_operator", "operpass")


# ─────────────────────────────────────────────────────────────────────────────
# Sample job run fixture
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def completed_job(db, operator_user) -> JobRun:
    """A finished (successful) job owned by the operator user."""
    import json
    from datetime import datetime, timedelta

    job = JobRun(
        user_id=operator_user.id,
        converter="tec-suite",
        flags_json=json.dumps({"jobs": 4, "verbose": True, "cleanup": True}),
        rinex_path="Configured from environment variable RINEX_DATA_PATH_HOST",
        output_path="/app/out",
        container_id="abc123def456",
        status="success",
        started_at=datetime.now(UTC) - timedelta(seconds=120),
        finished_at=datetime.now(UTC),
        exit_code=0,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job
