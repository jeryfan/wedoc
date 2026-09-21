"""Shared fixtures for module endpoint tests (dev PG/Redis, see test_auth.py)."""

import os
import time

import pytest

os.environ.setdefault("PUBLIC_ORIGIN", "http://localhost:3000")
os.environ.setdefault("STORAGE_PREFIX", "http://localhost:3000")
os.environ.setdefault("BACKEND_CACHE_REDIS_URI", "redis://default:wedoc@127.0.0.1:46380")
os.environ.setdefault("PRISMA_DATABASE_URL", "postgresql://wedoc:wedoc@127.0.0.1:42346/wedoc")
os.environ.setdefault("SECRET_KEY", "refSecretKey000000")

import asyncpg
import httpx
import redis.asyncio as aioredis

from wedoc.config import get_settings
from wedoc.core.cache import close_cache
from wedoc.core.mailer import reset_mailer
from wedoc.db import engine as db_engine
from wedoc.db.migrator import migrate_all
from wedoc.main import create_app

_migrated = False


@pytest.fixture
async def client():
    global _migrated
    settings = get_settings()
    try:
        conn = await asyncpg.connect(settings.meta_database_dsn)
        await conn.close()
        redis_client = aioredis.from_url(settings.backend_cache_redis_uri)
        await redis_client.ping()
        await redis_client.aclose()
    except Exception:
        pytest.skip("dev PG/Redis unavailable")
    if not _migrated:
        await migrate_all(settings.meta_database_dsn)
        _migrated = True
    await db_engine.init_db()
    # raise_app_exceptions=False: starlette's ServerErrorMiddleware re-raises
    # after producing the 500 body; the harness wants the response, not the raise
    transport = httpx.ASGITransport(app=create_app(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    # the cache/redis singleton is loop-bound: tear it down on the same loop
    await close_cache()
    reset_mailer()
    await db_engine.close_db()


@pytest.fixture
async def db():
    conn = await asyncpg.connect(get_settings().meta_database_dsn)
    yield conn
    await conn.close()


def uniq_email() -> str:
    return f"test-{int(time.time() * 1000)}-{os.urandom(3).hex()}@example.com"


async def signup(client: httpx.AsyncClient, email: str | None = None, password: str = "Passw0rd!1"):
    email = email or uniq_email()
    resp = await client.post("/api/auth/signup", json={"email": email, "password": password})
    assert resp.status_code == 201, resp.text
    return email, resp
