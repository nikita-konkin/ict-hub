"""
database.py — SQLAlchemy setup for SQLite.

We use a single SQLite file mounted inside a Docker volume so data persists
across container restarts. The check_same_thread=False flag is required for
SQLite when used with FastAPI's async request handling.
"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import DATABASE_URL

# connect_args is SQLite-specific: allows sharing a connection across threads
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False,  # set to True for SQL query logging during debugging
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record):
    """
    WAL mode lets readers and writers proceed concurrently instead of
    locking the whole file on every write. Without it, the SSE job-log
    pollers (app/jobs.py, one query every 0.5s per open stream) contend
    with job-event writers and normal page reads for the same lock.
    """
    if not DATABASE_URL.startswith("sqlite"):
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """Base class for all ORM models."""
    pass


def get_db():
    """
    FastAPI dependency that provides a database session per request.
    Uses a try/finally to ensure the session is always closed, even on errors.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
