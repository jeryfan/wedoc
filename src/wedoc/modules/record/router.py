"""Routes for /api/table/:tableId/record.

Ports record-open-api.controller.ts. The socket snapshot-bulk/doc-ids fallback
endpoints (M3 realtime) are implemented here; attachments need the storage
stack and collaborators land with presence.
"""

import json
import math
import re
from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from ...core.errors import ApiError, HttpErrorCode
from ...core.query import query_array, query_list
from ...core.security.auth import auth_guard, permissions
from ...core.security.permissions import permission_guard
from ...core.validation import read_json_body
from .schemas import (
    InsertAttachmentBody,
    RecordBulkPatchBody,
    RecordCreateBody,
    RecordInsertOrder,
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


def _record_ids_from_query(params: Any) -> list[str]:
    # deleteRecords ships recordIds as an array; the reference parses the query
    # with qs, so the frontend's axios `recordIds[]=` bracket form is an array
    # (even with one element), a plain repeated key is an array, a single plain
    # `recordIds=x` is a scalar (rejected), and an absent key is undefined.
    ids = query_array(params, "recordIds", required=True)
    assert ids is not None  # required=True never returns None
    return ids


# Verbatim zod-validation-error output for getRecordsRoSchema's take/skip
# failing branches (packages/openapi/src/record/get-list.ts:200-231); kept
# literal so wire messages stay identical.
_TAKE_NAN_ERROR = 'Invalid input: expected number, received NaN at "take"'
_TAKE_MIN_ERROR = 'You should at least take 1 record at "take"'
_TAKE_MAX_ERROR = "Can't take more than 1000 records, please reduce take count at \"take\""
_SKIP_NAN_ERROR = 'Invalid input: expected number, received NaN at "skip"'
_SKIP_MIN_ERROR = 'You can not skip a negative count of records at "skip"'
_FIELD_KEY_TYPE_ERROR = (
    'Error fieldKeyType, You should set it to "name" or "id" or "dbFieldName" at "fieldKeyType"'
)
_CELL_FORMAT_ERROR = 'Error cellFormat, You should set it to "json" or "text" at "cellFormat"'

# zod coerces take/skip through String(Number(x)); a non-numeric string becomes
# NaN, which the piped z.number() rejects as invalid_type.
_JS_NUMBER_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")


def _js_number(raw: str) -> float:
    text = raw.strip()
    if not text:
        return 0.0
    if not _JS_NUMBER_RE.match(text):
        return float("nan")
    return float(text)


def _page_number(value: float) -> int | float:
    return int(value) if value.is_integer() else value


def _validate_take(raw: str, default: int, errors: list[str]) -> int | float:
    number = _js_number(raw)
    if math.isnan(number):
        errors.append(_TAKE_NAN_ERROR)
        return default
    if number < 1:
        errors.append(_TAKE_MIN_ERROR)
        return default
    if number > 1000:
        errors.append(_TAKE_MAX_ERROR)
        return default
    return _page_number(number)


def _validate_skip(raw: str, errors: list[str]) -> int | float:
    number = _js_number(raw)
    if math.isnan(number):
        errors.append(_SKIP_NAN_ERROR)
        return 0
    if number < 0:
        errors.append(_SKIP_MIN_ERROR)
        return 0
    return _page_number(number)


def _parse_list_query(params: Any) -> tuple[int | float, int | float, str, str]:
    # getRecordsRoSchema reports issues in field order: cellFormat, fieldKeyType,
    # then take, skip. Absent params fall back to defaults without an error.
    errors: list[str] = []
    cell_format = params.get("cellFormat")
    if cell_format is not None and cell_format not in ("json", "text"):
        errors.append(_CELL_FORMAT_ERROR)
    field_key_type = params.get("fieldKeyType")
    if field_key_type is not None and field_key_type not in ("name", "id", "dbFieldName"):
        errors.append(_FIELD_KEY_TYPE_ERROR)
    take: int | float = 100
    if "take" in params:
        take = _validate_take(params["take"], 100, errors)
    skip: int | float = 0
    if "skip" in params:
        skip = _validate_skip(params["skip"], errors)
    if errors:
        raise ApiError(
            "Validation error: " + "; ".join(errors), HttpErrorCode.VALIDATION_ERROR
        )
    return take, skip, field_key_type or "name", "text" if cell_format == "text" else "json"


def _parse_record_query(params: Any) -> tuple[str, str]:
    # getRecordQuerySchema validates fieldKeyType / cellFormat the same way as the
    # list query (issue order: cellFormat, then fieldKeyType); no take/skip here.
    errors: list[str] = []
    cell_format = params.get("cellFormat")
    if cell_format is not None and cell_format not in ("json", "text"):
        errors.append(_CELL_FORMAT_ERROR)
    field_key_type = params.get("fieldKeyType")
    if field_key_type is not None and field_key_type not in ("name", "id", "dbFieldName"):
        errors.append(_FIELD_KEY_TYPE_ERROR)
    if errors:
        raise ApiError(
            "Validation error: " + "; ".join(errors), HttpErrorCode.VALIDATION_ERROR
        )
    return field_key_type or "name", "text" if cell_format == "text" else "json"


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
    take, skip, field_key_type, cell_format = _parse_list_query(params)
    ignore_view_query = (params.get("ignoreViewQuery") or "").lower() == "true"
    return await RecordService().list_records(
        tableId,
        field_key_type=field_key_type,
        projection=query_list(params, "projection") or None,
        view_id=params.get("viewId"),
        filter_param=_json_param(params.get("filter")),
        sort_param=_json_param(params.get("sort")),
        order_by=_json_param(params.get("orderBy")),
        group_by=_json_param(params.get("groupBy")),
        collapsed_group_ids=_json_param(params.get("collapsedGroupIds")),
        tql=params.get("filterByTql"),
        search=query_array(params, "search", expected="tuple"),
        ignore_view_query=ignore_view_query,
        take=take,
        skip=skip,
        cursor=params.get("cursor"),
        cell_format=cell_format,
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
@permissions("table_record_history|read")
async def get_table_history(tableId: str, request: Request) -> dict[str, Any]:
    params = request.query_params
    return await RecordService().get_history(
        tableId,
        None,
        cursor=params.get("cursor"),
        start_date=params.get("startDate"),
        end_date=params.get("endDate"),
        field_ids=query_list(params, "fieldIds") or None,
        created_by_ids=query_list(params, "createdByIds") or None,
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
    field_key_type, cell_format = _parse_record_query(params)
    return await RecordService().get_record(
        tableId,
        recordId,
        field_key_type=field_key_type,
        projection=query_list(params, "projection") or None,
        cell_format=cell_format,
    )


@router.get("/{recordId}/history", status_code=200)
@permissions("record|update")
async def get_record_history(tableId: str, recordId: str, request: Request) -> dict[str, Any]:
    params = request.query_params
    return await RecordService().get_history(
        tableId,
        recordId,
        cursor=params.get("cursor"),
        start_date=params.get("startDate"),
        end_date=params.get("endDate"),
        field_ids=query_list(params, "fieldIds") or None,
        created_by_ids=query_list(params, "createdByIds") or None,
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
    record_ids = _record_ids_from_query(request.query_params)
    return await RecordService().delete_records(tableId, record_ids)


@router.post("/form-submit", status_code=201)
@permissions("record|create")
async def form_submit(tableId: str, request: Request) -> dict[str, Any]:
    body = RecordSubmitBody.zod_validate(await read_json_body(request))
    return await RecordService().form_submit(tableId, body)


@router.post("/{recordId}/duplicate", status_code=201)
@permissions("record|create")
async def duplicate_record(tableId: str, recordId: str, request: Request) -> dict[str, Any]:
    # optionalRecordOrderSchema: the body IS the order object, and null/empty
    # object normalizes to no ordering.
    raw = await read_json_body(request)
    order = (
        RecordInsertOrder.zod_validate(raw) if isinstance(raw, dict) and raw else None
    )
    return await RecordService().duplicate_record(tableId, recordId, order)


@router.get("/{recordId}/status", status_code=200)
@permissions("table|read")
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
        search=query_array(params, "search", expected="tuple"),
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
