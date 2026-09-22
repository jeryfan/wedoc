"""Routes for /api/table/:tableId/record.

Ports record-open-api.controller.ts. history/form-submit/TQL search land in
later slices; attachments need the storage stack; collaborators and the
socket snapshot/doc-ids endpoints are M3 realtime.
"""

import json
from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from ...core.security.auth import auth_guard, permissions
from ...core.security.permissions import permission_guard
from ...core.validation import read_json_body
from .schemas import (
    RecordBulkPatchBody,
    RecordCreateBody,
    RecordPatchBody,
    RecordSubmitBody,
)
from .service import RecordService

router = APIRouter(
    prefix="/api/table/{tableId}/record",
    dependencies=[Depends(auth_guard), Depends(permission_guard)],
)


def _json_param(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


@router.get("", status_code=200)
@permissions("record|read")
async def list_records(tableId: str, request: Request) -> dict[str, Any]:
    params = request.query_params
    take = int(params.get("take") or 1000)
    skip = int(params.get("skip") or 0)
    return await RecordService().list_records(
        tableId,
        field_key_type=params.get("fieldKeyType") or "name",
        projection=params.getlist("projection") or None,
        view_id=params.get("viewId"),
        filter_param=_json_param(params.get("filter")),
        sort_param=_json_param(params.get("sort")),
        take=take,
        skip=skip,
        cursor=params.get("cursor"),
    )


@router.post("", status_code=201)
@permissions("record|create")
async def create_records(tableId: str, request: Request) -> dict[str, Any]:
    body = RecordCreateBody.zod_validate(await read_json_body(request))
    return await RecordService().create_records(tableId, body)


@router.patch("", status_code=200)
@permissions("record|update")
async def update_records(tableId: str, request: Request) -> list[dict[str, Any]]:
    body = RecordBulkPatchBody.zod_validate(await read_json_body(request))
    record_ids = request.query_params.getlist("recordIds")
    return await RecordService().update_records(tableId, record_ids, body)


@router.get("/history", status_code=200)
@permissions("record|read")
async def get_table_history(tableId: str, request: Request) -> dict[str, Any]:
    params = request.query_params
    return await RecordService().get_history(
        tableId,
        None,
        cursor=params.get("cursor"),
        start_date=params.get("startDate"),
        end_date=params.get("endDate"),
        field_ids=params.getlist("fieldIds") or None,
        created_by_ids=params.getlist("createdByIds") or None,
    )


@router.get("/{recordId}", status_code=200)
@permissions("record|read")
async def get_record(tableId: str, recordId: str, request: Request) -> dict[str, Any]:
    field_key_type = request.query_params.get("fieldKeyType") or "name"
    return await RecordService().get_record(tableId, recordId, field_key_type)


@router.get("/{recordId}/history", status_code=200)
@permissions("record|read")
async def get_record_history(tableId: str, recordId: str, request: Request) -> dict[str, Any]:
    params = request.query_params
    return await RecordService().get_history(
        tableId,
        recordId,
        cursor=params.get("cursor"),
        start_date=params.get("startDate"),
        end_date=params.get("endDate"),
        field_ids=params.getlist("fieldIds") or None,
        created_by_ids=params.getlist("createdByIds") or None,
    )


@router.patch("/{recordId}", status_code=200)
@permissions("record|update")
async def update_record(tableId: str, recordId: str, request: Request) -> dict[str, Any]:
    body = RecordPatchBody.zod_validate(await read_json_body(request))
    return await RecordService().update_record(tableId, recordId, body)


@router.delete("/{recordId}", status_code=200)
@permissions("record|delete")
async def delete_record(tableId: str, recordId: str):
    result = await RecordService().delete_record(tableId, recordId)
    if result is None:
        return Response(status_code=200)
    return result


@router.delete("", status_code=200)
@permissions("record|delete")
async def delete_records(tableId: str, request: Request) -> dict[str, Any]:
    record_ids = request.query_params.getlist("recordIds")
    return await RecordService().delete_records(tableId, record_ids)


@router.post("/form-submit", status_code=201)
@permissions("record|create")
async def form_submit(tableId: str, request: Request) -> dict[str, Any]:
    body = RecordSubmitBody.zod_validate(await read_json_body(request))
    return await RecordService().form_submit(tableId, body)


@router.post("/{recordId}/duplicate", status_code=201)
@permissions("record|create")
async def duplicate_record(tableId: str, recordId: str) -> dict[str, Any]:
    return await RecordService().duplicate_record(tableId, recordId)


@router.get("/{recordId}/status", status_code=200)
@permissions("record|read")
async def get_record_status(tableId: str, recordId: str) -> dict[str, Any]:
    return await RecordService().get_status(tableId, recordId)


@router.post("/{recordId}/{fieldId}/auto-fill", status_code=201)
@permissions("record|update")
async def auto_fill_cell(tableId: str, recordId: str, fieldId: str) -> dict[str, Any]:
    return {"taskId": ""}


@router.post("/{recordId}/{fieldId}/button-click", status_code=201)
@permissions("record|update")
async def button_click(tableId: str, recordId: str, fieldId: str) -> Response:
    from ...core.errors import ApiError, HttpErrorCode

    raise ApiError(
        "Field is not a Button field",
        HttpErrorCode.VALIDATION_ERROR,
        {
            "domainCode": "button.field_type_invalid",
            "domainTags": ["validation"],
            "details": {"fieldId": fieldId},
        },
    )


@router.post("/{recordId}/{fieldId}/button-reset", status_code=201)
@permissions("record|update")
async def button_reset(tableId: str, recordId: str, fieldId: str) -> Response:
    from ...core.errors import ApiError, HttpErrorCode

    raise ApiError(
        "Field is not a Button field",
        HttpErrorCode.VALIDATION_ERROR,
        {
            "domainCode": "button.field_type_invalid",
            "domainTags": ["validation"],
            "details": {"fieldId": fieldId},
        },
    )
