"""Routes for /api/table/:tableId/selection.

Ports selection.controller.ts. Implements range-to-id, copy, clear, delete, the
paste family (paste / paste-by-id / temporaryPaste / copy-by-id / clear-by-id /
delete-by-id) and their SSE stream variants. SSE events mirror the reference V2
stream shapes (which the reference runs for freshly-created bases via the canary).
"""

import json
import re
from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from ...core.errors import ApiError, HttpErrorCode
from ...core.query import query_array
from ...core.security.auth import auth_guard, permissions
from ...core.security.permissions import permission_guard
from ...core.validation import read_json_body
from .schemas import (
    DeleteByIdBody,
    PasteByIdBody,
    PasteByIdStreamBody,
    PasteRoBody,
    RangesRoBody,
    RangeToIdReturnType,
    SelectionIdMutationBody,
    SelectionIdsBody,
    TemporaryPasteBody,
)
from .service import IdReturnType, SelectionService

router = APIRouter(
    prefix="/api/table/{tableId}/selection",
    dependencies=[Depends(auth_guard), Depends(permission_guard)],
)

_SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _ranges_from_query(params: Any) -> list:
    raw = params.get("ranges")
    if raw is None:
        raise ApiError(
            'Validation error: Invalid input: expected string, received undefined at "ranges"',
            HttpErrorCode.VALIDATION_ERROR,
        )
    # a malformed ranges string is a raw JSON.parse throw upstream (unhandled ->
    # 500); only a parsed-but-wrong-shape value is reported as a 400 zod error.
    parsed = json.loads(raw)
    if isinstance(parsed, list) and len(parsed) < 1:
        raise ApiError(
            "Validation error: The range parameter must be a valid 2D array "
            'with even length. at "ranges"',
            HttpErrorCode.VALIDATION_ERROR,
        )
    # each range element must be a [number, number] pair; a wrong shape reports
    # the reference's per-position zod errors (pathed at "ranges") instead of 500.
    errors: list[str] = []
    for element in parsed if isinstance(parsed, list) else []:
        if not isinstance(element, list):
            errors.append(
                f'Invalid input: expected array, received {_js_type(element)} at "ranges"'
            )
            continue
        for position in element:
            if isinstance(position, bool) or not isinstance(position, (int, float)):
                errors.append(
                    f'Invalid input: expected number, received {_js_type(position)} at "ranges"'
                )
    if errors:
        raise ApiError("Validation error: " + "; ".join(errors), HttpErrorCode.VALIDATION_ERROR)
    return parsed


def _js_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def _query_from_params(params: Any) -> dict[str, Any]:
    def _json_p(name: str) -> Any:
        raw = params.get(name)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    query: dict[str, Any] = {
        "viewId": params.get("viewId"),
        "filter": _json_p("filter"),
        "orderBy": _json_p("orderBy"),
        "ranges": _ranges_from_query(params),
        "type": params.get("type"),
        "projection": query_array(params, "projection"),
    }
    return query


def _sse(events: list[dict[str, Any]]) -> Response:
    body = "".join(
        f"data: {json.dumps(e, ensure_ascii=False, separators=(',', ':'))}\n\n"
        for e in events
    )
    return Response(content=body, media_type="text/event-stream", headers=_SSE_HEADERS)


def _paste_total_count(content: Any) -> int:
    if isinstance(content, list):
        return len(content)
    if isinstance(content, str):
        trimmed = content.strip()
        if not trimmed:
            return 0
        return len(re.split(r"\r?\n", trimmed))
    return 0


def _clear_stream_events(total: int, cleared_ids: list[str]) -> list[dict[str, Any]]:
    cleared = len(cleared_ids)
    events: list[dict[str, Any]] = [
        {
            "id": "progress",
            "phase": "preparing",
            "batchIndex": -1,
            "totalCount": 0,
            "processedCount": 0,
            "clearedCount": 0,
            "batchProcessedCount": 0,
            "batchClearedCount": 0,
        },
        {
            "id": "progress",
            "phase": "preparing",
            "batchIndex": -1,
            "totalCount": total,
            "processedCount": 0,
            "clearedCount": 0,
            "batchProcessedCount": 0,
            "batchClearedCount": 0,
        },
    ]
    if total:
        events.append(
            {
                "id": "progress",
                "phase": "clearing",
                "batchIndex": 0,
                "totalCount": total,
                "processedCount": total,
                "clearedCount": cleared,
                "batchProcessedCount": total,
                "batchClearedCount": cleared,
            }
        )
    events.append(
        {
            "id": "done",
            "totalCount": total,
            "processedCount": total,
            "clearedCount": cleared,
            "data": {"clearedCount": cleared, "clearedRecordIds": cleared_ids},
        }
    )
    return events


def _paste_stream_events(
    total: int, updated: int, created: int, created_ids: list[str]
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = [
        {
            "id": "progress",
            "phase": "preparing",
            "batchIndex": -1,
            "totalCount": 0,
            "processedCount": 0,
            "updatedCount": 0,
            "createdCount": 0,
            "batchProcessedCount": 0,
        },
        {
            "id": "progress",
            "phase": "preparing",
            "batchIndex": -1,
            "totalCount": total,
            "processedCount": 0,
            "updatedCount": 0,
            "createdCount": 0,
            "batchProcessedCount": 0,
        },
    ]
    if total:
        events.append(
            {
                "id": "progress",
                "phase": "pasting",
                "batchIndex": 0,
                "totalCount": total,
                "processedCount": total,
                "updatedCount": updated,
                "createdCount": created,
                "batchProcessedCount": total,
            }
        )
    events.append(
        {
            "id": "done",
            "totalCount": total,
            "processedCount": total,
            "updatedCount": updated,
            "createdCount": created,
            "data": {
                "updatedCount": updated,
                "createdCount": created,
                "createdRecordIds": created_ids,
            },
        }
    )
    return events


@router.get("/range-to-id", status_code=200)
@permissions("record|read")
async def get_ids_from_ranges(tableId: str, request: Request) -> dict[str, Any]:
    query = _query_from_params(request.query_params)
    return_type = request.query_params.get("returnType")
    RangeToIdReturnType.zod_validate({"returnType": return_type})
    query["returnType"] = return_type
    return await SelectionService().get_ids_from_ranges(tableId, query)


@router.get("/copy", status_code=200)
@permissions("record|read", "record|copy")
async def copy(tableId: str, request: Request) -> dict[str, Any]:
    query = _query_from_params(request.query_params)
    return await SelectionService().copy(tableId, query)


@router.patch("/clear", status_code=200)
@permissions("record|update")
async def clear(tableId: str, request: Request) -> Response:
    body = await read_json_body(request)
    RangesRoBody.zod_validate(body)
    window_id = request.headers.get("x-window-id")
    await SelectionService().clear(tableId, body, window_id)
    return Response(status_code=200)


@router.patch("/paste", status_code=200)
@permissions("record|update")
async def paste(tableId: str, request: Request) -> dict[str, Any]:
    body = await read_json_body(request)
    PasteRoBody.zod_validate(body)
    window_id = request.headers.get("x-window-id")
    result = await SelectionService().paste(tableId, body, window_id)
    return {"ranges": result["ranges"]}


@router.patch("/paste-by-id", status_code=200)
@permissions("record|update")
async def paste_by_id(tableId: str, request: Request) -> dict[str, Any]:
    body = await read_json_body(request)
    PasteByIdBody.zod_validate(body)
    window_id = request.headers.get("x-window-id")
    result = await SelectionService().paste_by_id(tableId, body, window_id)
    return result["vo"]


@router.patch("/temporaryPaste", status_code=200)
@permissions("record|read")
async def temporary_paste(tableId: str, request: Request) -> list[dict[str, Any]]:
    body = await read_json_body(request)
    TemporaryPasteBody.zod_validate(body)
    return await SelectionService().temporary_paste(tableId, body)


@router.post("/copy-by-id", status_code=201)
@permissions("record|read", "record|copy")
async def copy_by_id(tableId: str, request: Request) -> dict[str, Any]:
    body = await read_json_body(request)
    SelectionIdMutationBody.zod_validate(body)
    return await SelectionService().copy_by_id(tableId, body)


@router.patch("/clear-by-id", status_code=200)
@permissions("record|update")
async def clear_by_id(tableId: str, request: Request) -> Response:
    body = await read_json_body(request)
    SelectionIdMutationBody.zod_validate(body)
    window_id = request.headers.get("x-window-id")
    await SelectionService().clear_by_id(tableId, body, window_id)
    return Response(status_code=200)


@router.post("/delete-by-id", status_code=201)
@permissions("record|delete")
async def delete_by_id(tableId: str, request: Request) -> dict[str, Any]:
    body = await read_json_body(request)
    DeleteByIdBody.zod_validate(body)
    window_id = request.headers.get("x-window-id")
    return await SelectionService().delete_by_id(tableId, body, window_id)


@router.delete("/delete", status_code=200)
@permissions("record|delete")
async def delete(tableId: str, request: Request) -> dict[str, Any]:
    query = _query_from_params(request.query_params)
    window_id = request.headers.get("x-window-id")
    return await SelectionService().delete(tableId, query, window_id)


@router.get("/delete-stream", status_code=200)
@permissions("record|delete")
async def delete_stream(tableId: str, request: Request) -> Response:
    query = _query_from_params(request.query_params)
    window_id = request.headers.get("x-window-id")
    result = await SelectionService().delete(tableId, query, window_id)
    ids = result["ids"]
    n = len(ids)
    # Reference runs the V2 delete stream: initial preparing(0), preparing(N),
    # a single deleting batch, then done.
    events: list[dict[str, Any]] = [
        {
            "id": "progress",
            "phase": "preparing",
            "batchIndex": -1,
            "totalCount": 0,
            "deletedCount": 0,
            "batchDeletedCount": 0,
        },
        {
            "id": "progress",
            "phase": "preparing",
            "batchIndex": -1,
            "totalCount": n,
            "deletedCount": 0,
            "batchDeletedCount": 0,
        },
    ]
    if n:
        events.append(
            {
                "id": "progress",
                "phase": "deleting",
                "batchIndex": 0,
                "totalCount": n,
                "deletedCount": n,
                "batchDeletedCount": n,
            }
        )
    events.append(
        {
            "id": "done",
            "totalCount": n,
            "deletedCount": n,
            "data": {"deletedCount": n, "deletedRecordIds": ids},
        }
    )
    return _sse(events)


@router.patch("/clear-stream", status_code=200)
@permissions("record|update")
async def clear_stream(tableId: str, request: Request) -> Response:
    body = await read_json_body(request)
    RangesRoBody.zod_validate(body)
    window_id = request.headers.get("x-window-id")
    query = {
        "viewId": body.get("viewId"),
        "filter": body.get("filter"),
        "orderBy": body.get("orderBy"),
        "ranges": body["ranges"],
        "type": body.get("type"),
        "projection": body.get("projection"),
        "returnType": IdReturnType.RECORD_ID,
    }
    ids_result = await SelectionService().get_ids_from_ranges(tableId, query)
    total = len(ids_result.get("recordIds") or [])
    cleared_ids = await SelectionService().clear(tableId, body, window_id)
    return _sse(_clear_stream_events(total, cleared_ids))


@router.patch("/clear-by-id-stream", status_code=200)
@permissions("record|update")
async def clear_by_id_stream(tableId: str, request: Request) -> Response:
    body = await read_json_body(request)
    SelectionIdsBody.zod_validate(body)
    window_id = request.headers.get("x-window-id")
    record_ids = await SelectionService()._resolve_record_ids_from_ids(tableId, body)
    total = len(record_ids)
    cleared_ids = await SelectionService().clear_by_ids(tableId, body, window_id)
    return _sse(_clear_stream_events(total, cleared_ids))


@router.patch("/paste-stream", status_code=200)
@permissions("record|update")
async def paste_stream(tableId: str, request: Request) -> Response:
    body = await read_json_body(request)
    PasteRoBody.zod_validate(body)
    window_id = request.headers.get("x-window-id")
    total = _paste_total_count(body.get("content"))
    result = await SelectionService().paste(tableId, body, window_id)
    return _sse(
        _paste_stream_events(
            total,
            result["updatedCount"],
            result["createdCount"],
            result["createdRecordIds"],
        )
    )


@router.patch("/paste-by-id-stream", status_code=200)
@permissions("record|update")
async def paste_by_id_stream(tableId: str, request: Request) -> Response:
    body = await read_json_body(request)
    PasteByIdStreamBody.zod_validate(body)
    window_id = request.headers.get("x-window-id")
    total = _paste_total_count(body.get("content"))
    result = await SelectionService().paste_by_id(tableId, body, window_id)
    return _sse(
        _paste_stream_events(
            total,
            result["updatedCount"],
            result["createdCount"],
            result["createdRecordIds"],
        )
    )


@router.patch("/delete-by-id-stream", status_code=200)
@permissions("record|delete")
async def delete_by_id_stream(tableId: str, request: Request) -> Response:
    body = await read_json_body(request)
    SelectionIdsBody.zod_validate(body)
    window_id = request.headers.get("x-window-id")
    result = await SelectionService().delete_by_ids(tableId, body, window_id)
    ids = result["ids"]
    n = len(ids)
    events: list[dict[str, Any]] = [
        {
            "id": "progress",
            "phase": "preparing",
            "batchIndex": -1,
            "totalCount": 0,
            "deletedCount": 0,
            "batchDeletedCount": 0,
        },
        {
            "id": "progress",
            "phase": "preparing",
            "batchIndex": -1,
            "totalCount": n,
            "deletedCount": 0,
            "batchDeletedCount": 0,
        },
    ]
    if n:
        events.append(
            {
                "id": "progress",
                "phase": "deleting",
                "batchIndex": 0,
                "totalCount": n,
                "deletedCount": n,
                "batchDeletedCount": n,
            }
        )
    events.append(
        {
            "id": "done",
            "totalCount": n,
            "deletedCount": n,
            "data": {"deletedCount": n, "deletedRecordIds": ids},
        }
    )
    return _sse(events)


@router.get("/duplicate-stream", status_code=200)
@permissions("record|read", "record|create")
async def duplicate_stream(tableId: str, request: Request) -> Response:
    params = request.query_params
    query = _query_from_params(params)
    query["returnType"] = IdReturnType.RECORD_ID
    ids_result = await SelectionService().get_ids_from_ranges(tableId, query)
    source_ids = ids_result.get("recordIds") or []
    n = len(source_ids)
    # Reference runs the V2 duplicate stream: preparing(0), preparing(N), a
    # single duplicating batch, then done.
    events: list[dict[str, Any]] = [
        {
            "id": "progress",
            "phase": "preparing",
            "batchIndex": -1,
            "totalCount": 0,
            "duplicatedCount": 0,
            "batchDuplicatedCount": 0,
        },
        {
            "id": "progress",
            "phase": "preparing",
            "batchIndex": -1,
            "totalCount": n,
            "duplicatedCount": 0,
            "batchDuplicatedCount": 0,
        },
    ]
    if not source_ids:
        events.append(
            {
                "id": "done",
                "totalCount": 0,
                "duplicatedCount": 0,
                "data": {"duplicatedCount": 0, "duplicatedRecordIds": []},
            }
        )
        return _sse(events)
    duplicated = await SelectionService().duplicate_record_ids(tableId, query)
    events.append(
        {
            "id": "progress",
            "phase": "duplicating",
            "batchIndex": 0,
            "totalCount": n,
            "duplicatedCount": len(duplicated),
            "batchDuplicatedCount": len(duplicated),
        }
    )
    events.append(
        {
            "id": "done",
            "totalCount": n,
            "duplicatedCount": len(duplicated),
            "data": {
                "duplicatedCount": len(duplicated),
                "duplicatedRecordIds": duplicated,
            },
        }
    )
    return _sse(events)
