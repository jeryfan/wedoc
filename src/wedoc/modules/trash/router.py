"""Routes for /api/trash.

Ports trash.controller.ts (v1 paths). The v2 canary headers / useV2 branching
and the record-snapshot table-trash paths are deferred (see service docstring).
Route order mirrors upstream so literal paths (items / reset-items / restore*)
win over the generic /{trashId} matcher.
"""

from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from ...core.errors import ApiError, HttpErrorCode
from ...core.security.auth import auth_guard
from ...core.security.permissions import permission_guard
from .schemas import TrashItemsRo, TrashRo
from .service import TrashService

router = APIRouter(
    prefix="/api/trash", dependencies=[Depends(auth_guard), Depends(permission_guard)]
)


@router.get("", status_code=200)
async def get_trash(request: Request) -> dict[str, Any]:
    params = request.query_params
    raw: dict[str, Any] = {}
    for key in ("spaceId", "resourceType"):
        if key in params:
            raw[key] = params[key]
    query = TrashRo.zod_validate(raw)
    return await TrashService().get_trash(query.resourceType, query.spaceId)


@router.get("/items", status_code=200)
async def get_trash_items(request: Request) -> dict[str, Any]:
    params = request.query_params
    raw: dict[str, Any] = {}
    for key in ("resourceId", "resourceType", "cursor"):
        if key in params:
            raw[key] = params[key]
    query = TrashItemsRo.zod_validate(raw)
    page_size = 20
    if "pageSize" in params:
        try:
            page_size = int(params["pageSize"])
        except ValueError as exc:
            raise ApiError(
                "Validation error: Invalid input: expected number at \"pageSize\"",
                HttpErrorCode.VALIDATION_ERROR,
            ) from exc
        if page_size < 1 or page_size > 20:
            raise ApiError(
                "Validation error: Too big: expected number to be <=20 at \"pageSize\"",
                HttpErrorCode.VALIDATION_ERROR,
            )
    return await TrashService().get_trash_items(
        query.resourceId, query.resourceType, query.cursor, page_size
    )


@router.get("/{trashId}/records", status_code=200)
async def get_trash_item_records(trashId: str, request: Request) -> dict[str, Any]:
    params = request.query_params
    table_id = params.get("tableId")
    if not table_id:
        raise ApiError(
            'Validation error: Invalid input: expected string, received undefined at "tableId"',
            HttpErrorCode.VALIDATION_ERROR,
        )
    take = 50
    if params.get("take"):
        try:
            take = int(params["take"])
        except ValueError:
            take = 50
    return await TrashService().get_table_trash_item_records(
        trashId, table_id, params.get("cursor"), take
    )


@router.post("/restore/{trashId}", status_code=201)
async def restore_trash(trashId: str, request: Request) -> Response:
    table_id = request.query_params.get("tableId")
    await TrashService().restore_trash(trashId, table_id)
    return Response(status_code=201)


@router.post("/restore-field/{trashId}/stream", status_code=201)
async def restore_field_trash_stream(trashId: str, request: Request) -> Response:
    # Deferred: field-restore SSE needs the v2 record-value pipeline.
    body = 'data: {"id":"done","totalCount":0,"updatedCount":0}\n\n'
    return Response(
        content=body,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.delete("/reset-items", status_code=200)
async def reset_trash_items(request: Request) -> Response:
    params = request.query_params
    raw: dict[str, Any] = {}
    for key in ("resourceId", "resourceType", "cursor"):
        if key in params:
            raw[key] = params[key]
    query = TrashItemsRo.zod_validate(raw)
    await TrashService().reset_trash_items(query.resourceId, query.resourceType)
    return Response(status_code=200)


@router.delete("/{trashId}", status_code=200)
async def delete_trash(trashId: str) -> Response:
    await TrashService().delete(trashId)
    return Response(status_code=200)
