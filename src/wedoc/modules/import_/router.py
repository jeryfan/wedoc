"""Routes for /api/import — ports import-open-api.controller.ts (legacy V1)."""

import json
from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from ...core.errors import ApiError
from ...core.security.auth import auth_guard, permissions, token_access
from ...core.security.permissions import permission_guard
from ...core.validation import read_json_body
from .schemas import AnalyzeRo, ImportOptionRo, InplaceImportOptionRo
from .service import ImportService

router = APIRouter(
    prefix="/api/import",
    dependencies=[Depends(auth_guard), Depends(permission_guard)],
)

_SSE_HEADERS = {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _sse(events: list[dict[str, Any]]) -> Response:
    body = "".join(
        f"data: {json.dumps(e, ensure_ascii=False, separators=(',', ':'))}\n\n"
        for e in events
    )
    return Response(content=body, media_type="text/event-stream", headers=_SSE_HEADERS)


def _progress_event() -> dict[str, Any]:
    return {
        "id": "progress",
        "phase": "preparing",
        "sheetIndex": 0,
        "sheetCount": 1,
        "batchIndex": -1,
        "totalCount": 0,
        "processedCount": 0,
        "importedCount": 0,
        "sheetTotalCount": 0,
        "sheetProcessedCount": 0,
        "batchProcessedCount": 0,
    }


def _error_event(message: str) -> dict[str, Any]:
    return {
        "id": "error",
        "phase": "importing",
        "sheetIndex": 0,
        "sheetCount": 1,
        "batchIndex": -1,
        "totalCount": 0,
        "processedCount": 0,
        "importedCount": 0,
        "message": message,
    }


@router.get("/analyze", status_code=200)
@token_access()
async def analyze(request: Request) -> dict[str, Any]:
    params = request.query_params
    ro = AnalyzeRo.zod_validate(
        {"attachmentUrl": params.get("attachmentUrl"), "fileType": params.get("fileType")}
    )
    return await ImportService().analyze(ro)


@router.get("/status/{tableId}", status_code=200)
@permissions("base|table_import")
@token_access()
async def get_import_status(tableId: str) -> dict[str, Any]:
    return await ImportService().get_import_status(tableId)


@router.post("/{baseId}", status_code=201)
@permissions("base|table_import")
@token_access()
async def create_table_from_import(baseId: str, request: Request) -> list[dict[str, Any]]:
    ro = ImportOptionRo.zod_validate(await read_json_body(request))
    return await ImportService().create_table_from_import(baseId, ro)


@router.post("/{baseId}/stream", status_code=200)
@permissions("base|table_import")
@token_access()
async def create_table_from_import_stream(baseId: str, request: Request) -> Response:
    ro = ImportOptionRo.zod_validate(await read_json_body(request))
    events: list[dict[str, Any]] = [_progress_event()]
    try:
        tables = await ImportService().create_table_from_import(baseId, ro)
        events.append(
            {
                "id": "done",
                "totalCount": 0,
                "processedCount": 0,
                "importedCount": 0,
                "data": {"tables": tables, "tableId": tables[0]["id"] if tables else None},
            }
        )
    except ApiError as exc:
        events.append(_error_event(exc.message))
    return _sse(events)


@router.patch("/{baseId}/{tableId}", status_code=200)
@permissions("table|import")
async def inplace_import_table(baseId: str, tableId: str, request: Request) -> Response:
    ro = InplaceImportOptionRo.zod_validate(await read_json_body(request))
    await ImportService().inplace_import_table(baseId, tableId, ro)
    return Response(status_code=200)


@router.patch("/{baseId}/{tableId}/stream", status_code=200)
@permissions("table|import")
async def inplace_import_table_stream(
    baseId: str, tableId: str, request: Request
) -> Response:
    ro = InplaceImportOptionRo.zod_validate(await read_json_body(request))
    events: list[dict[str, Any]] = [_progress_event()]
    try:
        await ImportService().inplace_import_table(baseId, tableId, ro)
        events.append(
            {
                "id": "done",
                "totalCount": 0,
                "processedCount": 0,
                "importedCount": 0,
                "data": {"tableId": tableId},
            }
        )
    except ApiError as exc:
        events.append(_error_event(exc.message))
    return _sse(events)
