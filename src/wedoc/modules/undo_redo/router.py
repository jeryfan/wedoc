"""Routes for /api/table/:tableId/undo-redo (ports undo-redo.controller.ts)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse

from ...compat import undo_redo_engine_header
from ...core import cls
from ...core.security.auth import auth_guard, permissions
from ...core.security.permissions import permission_guard
from .service import UndoRedoService, engine_for

router = APIRouter(
    prefix="/api/table/{tableId}/undo-redo",
    dependencies=[Depends(auth_guard), Depends(permission_guard)],
)


def _window_id(request: Request) -> str | None:
    return request.headers.get("x-window-id") or cls.get("windowId")


@router.post("/undo", status_code=201)
@permissions("table|read")
async def undo(tableId: str, request: Request, response: Response) -> dict[str, Any]:
    result = await UndoRedoService().undo(tableId, _window_id(request))
    response.headers[undo_redo_engine_header()] = engine_for(result)
    return result


@router.post("/redo", status_code=201)
@permissions("table|read")
async def redo(tableId: str, request: Request, response: Response) -> dict[str, Any]:
    result = await UndoRedoService().redo(tableId, _window_id(request))
    response.headers[undo_redo_engine_header()] = engine_for(result)
    return result


def _sse(events: AsyncIterator[dict[str, Any]]) -> StreamingResponse:
    async def stream() -> AsyncIterator[str]:
        async for event in events:
            yield f"data: {json.dumps(event, separators=(',', ':'))}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            # explicit content-type suppresses Starlette's "; charset=utf-8"
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/undo-stream")
@permissions("table|read")
async def undo_stream(tableId: str, request: Request) -> StreamingResponse:
    return _sse(UndoRedoService().undo_stream(tableId, _window_id(request)))


@router.post("/redo-stream")
@permissions("table|read")
async def redo_stream(tableId: str, request: Request) -> StreamingResponse:
    return _sse(UndoRedoService().redo_stream(tableId, _window_id(request)))
