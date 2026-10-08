"""Database setup.  Schema is modeled for Alembic; create_all is local-dev only."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings, get_settings


def create_engine_for_settings(settings: Settings | None = None) -> Engine:
    settings = settings or get_settings()
    connect_args: dict[str, object] = {}
    if settings.database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_engine(
        settings.database_url, future=True, pool_pre_ping=True, connect_args=connect_args
    )


def create_session_factory(settings: Settings | None = None) -> sessionmaker[Session]:
    return sessionmaker(
        bind=create_engine_for_settings(settings), autoflush=False, expire_on_commit=False
    )


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def initialize_local_schema(factory: sessionmaker[Session]) -> None:
    """Convenience for local development; production must run Alembic migrations."""
    from .models import Base

    Base.metadata.create_all(factory.kw["bind"])
