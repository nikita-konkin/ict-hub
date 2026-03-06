"""
database.py — SQLAlchemy setup for SQLite.

We use a single SQLite file mounted inside a Docker volume so data persists
across container restarts. The check_same_thread=False flag is required for
SQLite when used with FastAPI's async request handling.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import DATABASE_URL

# connect_args is SQLite-specific: allows sharing a connection across threads
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False,  # set to True for SQL query logging during debugging
)

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
