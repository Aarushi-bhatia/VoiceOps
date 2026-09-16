"""Async engine / session factory plus schema bootstrap."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings, get_settings

# Imported for its side effect: registers every table on Base.metadata.
from app.db import models as _models  # noqa: F401
from app.db.base import Base

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _build_engine(settings: Settings) -> AsyncEngine:
    kwargs: dict[str, object] = {"echo": settings.db_echo, "future": True}
    if settings.is_sqlite:
        # SQLite has no server-side pool to size; keep connections checked out
        # long enough for the async driver.
        kwargs["connect_args"] = {"timeout": 30}
    else:
        kwargs.update(pool_size=10, max_overflow=20, pool_pre_ping=True, pool_recycle=1800)
    return create_async_engine(settings.database_url, **kwargs)


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = _build_engine(get_settings())
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(), expire_on_commit=False, autoflush=False
        )
    return _sessionmaker


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Transactional scope for background work (worker, scripts)."""
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency. Commits on a clean request, rolls back otherwise."""
    async with session_scope() as session:
        yield session


async def init_models() -> None:
    """Create tables if they do not exist.

    Fine for local/dev and for the SQLite fallback. A real deployment should
    run Alembic migrations instead; this is idempotent either way.
    """
    engine = get_engine()
    async with engine.begin() as conn:
        if get_settings().is_sqlite:
            from sqlalchemy import text

            await conn.execute(text("PRAGMA journal_mode=WAL"))
            await conn.execute(text("PRAGMA foreign_keys=ON"))
        await conn.run_sync(Base.metadata.create_all)
    logger.info("database schema ready", extra={"url": _safe_url()})


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


def _safe_url() -> str:
    url = get_settings().database_url
    if "@" in url:
        scheme, _, rest = url.partition("://")
        return f"{scheme}://***@{rest.split('@', 1)[1]}"
    return url


def reset_engine_for_tests() -> None:
    """Drop cached engine/sessionmaker so a new DATABASE_URL takes effect."""
    global _engine, _sessionmaker
    _engine = None
    _sessionmaker = None
