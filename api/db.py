"""SQLAlchemy 2 engine/session wiring for the local SQLite DB (SPEC 3, 4)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


_settings = get_settings()

# check_same_thread=False so APScheduler jobs / CLI can share the engine.
engine = create_engine(
    _settings.sqlalchemy_url,
    echo=False,
    future=True,
    connect_args={"check_same_thread": False},
)


@event.listens_for(Engine, "connect")
def _enable_sqlite_fks(dbapi_connection, _connection_record) -> None:
    """SQLite does not enforce foreign keys unless asked, per-connection (SPEC 4).

    Registered on the base Engine class so every connection (app, CLI, tests,
    APScheduler) turns it on. Guards against corrupt writes like an FK pointing at
    a non-existent team.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db() -> None:
    """Create the data dir + all tables. Idempotent."""
    _settings.db_file.parent.mkdir(parents=True, exist_ok=True)
    _settings.raw_cache_dir.mkdir(parents=True, exist_ok=True)
    # Import models so they register on Base.metadata before create_all.
    from . import models  # noqa: F401

    Base.metadata.create_all(engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for scripts/services outside request handlers."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
