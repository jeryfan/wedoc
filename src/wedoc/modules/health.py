"""GET /health and /health/memory — terminus-compatible shapes."""

import os
import resource

import asyncpg
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..db.engine import meta_pool

router = APIRouter()


def memory_usage() -> dict:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {"rss": rss, "heapTotal": rss, "heapUsed": rss, "external": 0, "arrayBuffers": 0}


@router.get("/health")
async def health() -> JSONResponse:
    try:
        await meta_pool().fetchval("SELECT 1")
        body = {
            "status": "ok",
            "info": {"metaDatabase": {"status": "up"}},
            "error": {},
            "details": {"metaDatabase": {"status": "up"}},
        }
        return JSONResponse(body)
    except (asyncpg.PostgresError, OSError, AssertionError) as exc:
        detail = {"status": "down", "message": str(exc)}
        body = {
            "status": "error",
            "info": {},
            "error": {"metaDatabase": detail},
            "details": {"metaDatabase": detail},
        }
        return JSONResponse(body, status_code=503)


@router.get("/health/memory")
async def health_memory() -> dict:
    body: dict = {"memoryUsage": memory_usage()}
    pod = os.environ.get("HOSTNAME")
    if pod is not None:
        body["pod"] = pod
    return body
