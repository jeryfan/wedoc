"""Task queue with an in-memory synchronous fallback.

Ports the shape the upstream code relies on from BullMQ: a named queue with
``add(name, data, {jobId})``, and per-job ``getState`` / ``progress`` /
``returnvalue`` / ``failedReason`` lookups. Upstream's EventJobModule falls back
to synchronous in-process execution when no Redis-backed worker runs; this
module mirrors that: unless a dedicated worker process is running in ``arq``
mode, ``add`` executes the registered handler inline and records the terminal
state so a subsequent status read observes ``completed`` / ``failed``.

Job records live in the shared cache (Redis) so that when an out-of-process arq
worker is running it can update the same record the HTTP process reads back.
"""

from __future__ import annotations

import os
import time
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

from ..core.cache import get_cache

logger = structlog.get_logger(__name__)

# handler(data) -> return value (stats); registered by feature modules.
Handler = Callable[[dict[str, Any]], Awaitable[Any]]
_HANDLERS: dict[str, Handler] = {}

_JOB_TTL_SECONDS = 60 * 60  # 1h; matches the import manifest window


def task(name: str) -> Callable[[Handler], Handler]:
    """Register a queue task handler under ``name``."""

    def _wrap(fn: Handler) -> Handler:
        _HANDLERS[name] = fn
        return fn

    return _wrap


def worker_mode() -> str:
    # "arq" routes enqueues to the out-of-process worker; anything else runs
    # handlers inline in the request path.
    return os.environ.get("WEDOC_WORKER_MODE", "inline").lower()


def _job_key(job_id: str) -> str:
    return f"queue:job:{job_id}"


class Job:
    def __init__(self, record: dict[str, Any]) -> None:
        self._record = record

    @property
    def id(self) -> str:
        return self._record["id"]

    @property
    def data(self) -> dict[str, Any]:
        return self._record.get("data") or {}

    @property
    def progress(self) -> Any:
        return self._record.get("progress")

    @property
    def returnvalue(self) -> Any:
        return self._record.get("returnvalue")

    @property
    def failed_reason(self) -> str | None:
        return self._record.get("failedReason")

    async def get_state(self) -> str:
        return self._record.get("state", "unknown")


class TaskQueue:
    def __init__(self, name: str) -> None:
        self.name = name

    async def _persist(self, record: dict[str, Any]) -> None:
        await get_cache().set_detail(_job_key(record["id"]), record, _JOB_TTL_SECONDS)

    async def add(
        self,
        task_name: str,
        data: dict[str, Any],
        *,
        job_id: str,
    ) -> Job:
        record: dict[str, Any] = {
            "id": job_id,
            "queue": self.name,
            "task": task_name,
            "data": data,
            "state": "waiting",
            "progress": None,
            "returnvalue": None,
            "failedReason": None,
            "createdAt": int(time.time() * 1000),
        }

        if worker_mode() == "arq":
            await self._persist(record)
            await self._enqueue_arq(task_name, job_id)
            return Job(record)

        # Inline fallback: run to completion synchronously.
        record["state"] = "active"
        await self._persist(record)
        handler = _HANDLERS.get(task_name)
        if handler is None:
            record["state"] = "failed"
            record["failedReason"] = f"No handler registered for task {task_name}"
            await self._persist(record)
            return Job(record)
        try:
            result = await handler(data)
            record["state"] = "completed"
            record["returnvalue"] = result
        except Exception as exc:
            logger.error("queue task failed", task=task_name, job=job_id, error=str(exc))
            record["state"] = "failed"
            record["failedReason"] = str(exc)
        await self._persist(record)
        return Job(record)

    async def _enqueue_arq(self, task_name: str, job_id: str) -> None:
        from .arq_app import get_arq_pool

        pool = await get_arq_pool()
        await pool.enqueue_job("run_task", job_id, _job_id=job_id)

    async def get_job(self, job_id: str) -> Job | None:
        record = await get_cache().get(_job_key(job_id))
        if record is None:
            return None
        return Job(record)

    async def update_progress(self, job_id: str, progress: Any) -> None:
        record = await get_cache().get(_job_key(job_id))
        if record is None:
            return
        record["progress"] = progress
        await self._persist(record)


async def run_registered_task(job_id: str) -> Any:
    """Entry point for the out-of-process arq worker: load the job record,
    dispatch to its handler, and write back the terminal state."""
    cache = get_cache()
    record = await cache.get(_job_key(job_id))
    if record is None:
        logger.warning("arq run_task missing job record", job=job_id)
        return None
    record["state"] = "active"
    await cache.set_detail(_job_key(job_id), record, _JOB_TTL_SECONDS)
    handler = _HANDLERS.get(record["task"])
    if handler is None:
        record["state"] = "failed"
        record["failedReason"] = f"No handler registered for task {record['task']}"
        await cache.set_detail(_job_key(job_id), record, _JOB_TTL_SECONDS)
        return None
    try:
        result = await handler(record.get("data") or {})
        record["state"] = "completed"
        record["returnvalue"] = result
    except Exception as exc:
        record["state"] = "failed"
        record["failedReason"] = str(exc)
    await cache.set_detail(_job_key(job_id), record, _JOB_TTL_SECONDS)
    return record.get("returnvalue")
