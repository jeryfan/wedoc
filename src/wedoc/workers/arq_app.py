"""arq worker wiring for the out-of-process ``wedoc-worker`` entrypoint.

The worker reuses the shared Redis (``BACKEND_CACHE_REDIS_URI``) and registers
the same task handlers the HTTP process would run inline. Enqueued jobs carry
only the ``job_id``; the payload lives in the shared cache job record, so the
worker and the API converge on one record.
"""

from __future__ import annotations

from typing import Any, ClassVar

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from ..config import get_settings

# Importing feature modules registers their @task handlers as a side effect.
from . import tasks  # noqa: F401
from .queue import run_registered_task

_pool: ArqRedis | None = None


def _redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(get_settings().backend_cache_redis_uri)


async def get_arq_pool() -> ArqRedis:
    global _pool
    if _pool is None:
        _pool = await create_pool(_redis_settings())
    return _pool


async def run_task(_ctx: dict[str, Any], job_id: str) -> Any:
    return await run_registered_task(job_id)


class WorkerSettings:
    functions: ClassVar = [run_task]
    redis_settings = _redis_settings()
    max_jobs = 10


def run() -> None:
    """Console entrypoint for ``wedoc-worker``."""
    from arq.worker import run_worker

    run_worker(WorkerSettings)
