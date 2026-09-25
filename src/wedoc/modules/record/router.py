"""Routes for /api/table/:tableId/record.

Ports record-open-api.controller.ts. The socket snapshot-bulk/doc-ids fallback
endpoints (M3 realtime) are implemented here; attachments need the storage
stack and collaborators land with presence.
"""

import json
from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from ...core.errors import ApiError, HttpErrorCode
from ...core.security.auth import auth_guard, permissions
from ...core.security.permissions import permission_guard
from ...core.validation import read_json_body
from .schemas import (
    InsertAttachmentBody,
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


@router.post("/socket/snapshot-bulk", status_code=201)
@permissions("record|read")
async def socket_snapshot_bulk(tableId: str, request: Request) -> list[dict[str, Any]]:
    body = await read_json_body(request)
    ids = body.get("ids") or []
    projection = body.get("projection")
    return await RecordService().socket_snapshot_bulk(tableId, ids, projection)


@router.post("/socket/doc-ids", status_code=201)
@permissions("record|read")
async def socket_doc_ids(tableId: str, request: Request) -> dict[str, Any]:
    body = await read_json_body(request)
    return await RecordService().socket_doc_ids(tableId, body)


@router.get("", status_code=200)
@permissions("record|read")
async def list_records(tableId: str, request: Request) -> dict[str, Any]:
    params = request.query_params
    take = int(params.get("take") or 100)
    skip = int(params.get("skip") or 0)
    ignore_view_query = (params.get("ignoreViewQuery") or "").lower() == "true"
    return await RecordService().list_records(
        tableId,
        field_key_type=params.get("fieldKeyType") or "name",
        projection=params.getlist("projection") or None,
        view_id=params.get("viewId"),
        filter_param=_json_param(params.get("filter")),
        sort_param=_json_param(params.get("sort")),
        order_by=_json_param(params.get("orderBy")),
        group_by=_json_param(params.get("groupBy")),
        collapsed_group_ids=_json_param(params.get("collapsedGroupIds")),
        tql=params.get("filterByTql"),
        search=params.getlist("search") or None,
        ignore_view_query=ignore_view_query,
        take=take,
        skip=skip,
        cursor=params.get("cursor"),
        cell_format="text" if params.get("cellFormat") == "text" else "json",
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
    record_ids = [item.id for item in body.records]
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


@router.get("/collaborators", status_code=200)
@permissions("record|read")
async def get_collaborators(tableId: str, request: Request) -> list[dict[str, Any]]:
    params = request.query_params
    field_id = params.get("fieldId")
    if field_id is None:
        raise ApiError(
            'Validation error: Invalid input: expected string, received undefined at "fieldId"',
            HttpErrorCode.VALIDATION_ERROR,
        )
    query: dict[str, Any] = {"fieldId": field_id, "search": params.get("search")}
    if params.get("skip") is not None:
        query["skip"] = int(params["skip"])
    if params.get("take") is not None:
        query["take"] = int(params["take"])
    return await RecordService().get_records_collaborators(tableId, query)


@router.get("/{recordId}", status_code=200)
@permissions("record|read")
async def get_record(tableId: str, recordId: str, request: Request) -> dict[str, Any]:
    params = request.query_params
    return await RecordService().get_record(
        tableId,
        recordId,
        field_key_type=params.get("fieldKeyType") or "name",
        projection=params.getlist("projection") or None,
        cell_format="text" if params.get("cellFormat") == "text" else "json",
    )


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
async def get_record_status(tableId: str, recordId: str, request: Request) -> dict[str, Any]:
    params = request.query_params
    take = params.get("take")
    skip = params.get("skip")
    return await RecordService().get_status(
        tableId,
        recordId,
        view_id=params.get("viewId"),
        filter_param=_json_param(params.get("filter")),
        order_by=_json_param(params.get("orderBy")),
        group_by=_json_param(params.get("groupBy")),
        collapsed_group_ids=_json_param(params.get("collapsedGroupIds")),
        search=params.getlist("search") or None,
        ignore_view_query=(params.get("ignoreViewQuery") or "").lower() == "true",
        take=int(take) if take and take.isdigit() else 100,
        skip=int(skip) if skip and skip.isdigit() else 0,
    )


@router.post("/{recordId}/{fieldId}/auto-fill", status_code=201)
@permissions("record|update")
async def auto_fill_cell(tableId: str, recordId: str, fieldId: str) -> dict[str, Any]:
    return {"taskId": ""}


@router.post("/{recordId}/{fieldId}/uploadAttachment", status_code=201)
@permissions("record|update")
async def upload_attachment(
    tableId: str, recordId: str, fieldId: str, request: Request
) -> dict[str, Any]:
    form = await request.form()
    upload = form.get("file")
    file_url = form.get("fileUrl")
    file_arg: dict[str, Any] | None = None
    if upload is not None and hasattr(upload, "read"):
        data = await upload.read()
        file_arg = {
            "bytes": data,
            "filename": upload.filename or "file",
            "content_type": upload.content_type or "application/octet-stream",
        }
    return await RecordService().upload_attachment(
        tableId,
        recordId,
        fieldId,
        file_arg,
        file_url if isinstance(file_url, str) else None,
    )


@router.post("/{recordId}/{fieldId}/insertAttachment", status_code=201)
@permissions("record|update")
async def insert_attachment(
    tableId: str, recordId: str, fieldId: str, request: Request
) -> dict[str, Any]:
    body = InsertAttachmentBody.zod_validate(await read_json_body(request))
    return await RecordService().insert_attachment(
        tableId, recordId, fieldId, body.attachments, body.anchorId
    )


@router.post("/{recordId}/{fieldId}/button-click", status_code=201)
@permissions("record|read")
async def button_click(tableId: str, recordId: str, fieldId: str) -> dict[str, Any]:
    result = await RecordService().button_click(tableId, recordId, fieldId)
    return {**result, "runId": ""}


@router.post("/{recordId}/{fieldId}/button-reset", status_code=201)
@permissions("record|update")
async def button_reset(tableId: str, recordId: str, fieldId: str) -> dict[str, Any]:
    return await RecordService().reset_button(tableId, recordId, fieldId)
