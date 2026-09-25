"""Async database engines and pools (meta / data / BYODB custom)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ..config import get_settings

_meta_engine: AsyncEngine | None = None
_meta_pool: asyncpg.Pool | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


async def init_db() -> None:
    global _meta_engine, _meta_pool, _session_factory
    settings = get_settings()
    _meta_engine = create_async_engine(
        settings.sqlalchemy_dsn,
        pool_size=20,
        connect_args={"statement_cache_size": 0, "prepared_statement_cache_size": 0},
    )
    _session_factory = async_sessionmaker(_meta_engine, expire_on_commit=False)
    _meta_pool = await asyncpg.create_pool(
        settings.meta_database_dsn, min_size=2, max_size=20, statement_cache_size=0
    )


async def close_db() -> None:
    global _meta_engine, _meta_pool, _session_factory
    if _meta_pool:
        await _meta_pool.close()
    if _meta_engine:
        await _meta_engine.dispose()
    _meta_engine = _meta_pool = _session_factory = None


def meta_pool() -> asyncpg.Pool:
    assert _meta_pool is not None, "db not initialized"
    return _meta_pool


def meta_engine() -> AsyncEngine:
    assert _meta_engine is not None, "db not initialized"
    return _meta_engine


@asynccontextmanager
async def session() -> AsyncIterator[AsyncSession]:
    assert _session_factory is not None, "db not initialized"
    async with _session_factory() as s:
        yield s
