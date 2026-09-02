"""Database engine and session management."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .settings import get_settings

__all__ = ["engine", "SessionLocal", "get_db", "session_scope"]

_settings = get_settings()

engine = create_engine(
    _settings.database_url,
    pool_size=_settings.database_pool_size,
    max_overflow=5,
    pool_pre_ping=True,
    echo=_settings.database_echo,
    # Statement timeout so a pathological query cannot pin a connection
    # indefinitely and starve the pool during a filing deadline.
    connect_args={"options": "-c statement_timeout=30000"}
    if _settings.database_url.startswith("postgresql")
    else {},
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session.

    The session is rolled back on any exception rather than left in a failed
    state, because a connection returned to the pool mid-transaction poisons
    the next request that borrows it.
    """
    session = SessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for background jobs and scripts."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
