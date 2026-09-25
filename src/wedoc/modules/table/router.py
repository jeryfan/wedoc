"""Routes for /api/base/:baseId/table.

Ports table-open-api.controller.ts. The upstream controller is
@AllowAnonymous (RESOURCE); wedoc mirrors that only on the READ routes
(list, get, default-view-id, permission, socket snapshot-bulk/doc-ids) with
@allow_anonymous() + @permissions, so a public base-share / share-view /
template visitor can read while unshared anonymous access still 401s via the
permission guard. Write routes stay auth-only. The socket doc-ids/snapshot-bulk
endpoints (M3 realtime) are implemented here (table snapshot-bulk is
best-effort — see docs/api-parity-ledger.md); the search-index family (index
toggle/activated/search-vector-status/abnormal/repair) lives in
index_service.py.
"""

from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from ...core.security.auth import allow_anonymous, auth_guard, permissions
from ...core.security.permissions import permission_guard
from ...core.validation import read_json_body
from .index_service import TableIndexService
from .schemas import (
    CreateTableBody,
    DbTableNameBody,
    DuplicateTableBody,
    TableDescriptionBody,
    TableIconBody,
    TableNameBody,
    ToggleIndexBody,
    UpdateOrderBody,
)
from .service import TableService

router = APIRouter(
    prefix="/api/base/{baseId}/table",
    dependencies=[Depends(auth_guard), Depends(permission_guard)],
)


@router.post("", status_code=201)
@permissions("table|create")
async def create_table(baseId: str, request: Request) -> dict[str, Any]:
    body = CreateTableBody.zod_validate(await read_json_body(request))
    return await TableService().create_table(baseId, body)


@router.get("", status_code=200)
@permissions("table|read")
@allow_anonymous()
async def list_tables(baseId: str) -> list[dict[str, Any]]:
    return await TableService().list_tables(baseId)


@router.get("/socket/snapshot-bulk", status_code=200)
@permissions("table|read")
@allow_anonymous()
async def socket_snapshot_bulk(baseId: str, request: Request) -> list[dict[str, Any]]:
    ids = request.query_params.getlist("ids")
    return await TableService().socket_snapshot_bulk(baseId, ids)


@router.get("/socket/doc-ids", status_code=200)
@permissions("table|read")
@allow_anonymous()
async def socket_doc_ids(baseId: str, request: Request) -> dict[str, Any]:
    return await TableService().socket_doc_ids(baseId, dict(request.query_params))


@router.put("/{tableId}/name", status_code=200)
@permissions("table|update")
async def update_name(baseId: str, tableId: str, request: Request) -> Response:
    body = TableNameBody.zod_validate(await read_json_body(request))
    await TableService().update_name(baseId, tableId, body.name)
    return Response(status_code=200)


@router.put("/{tableId}/icon", status_code=200)
@permissions("table|update")
async def update_icon(baseId: str, tableId: str, request: Request) -> Response:
    body = TableIconBody.zod_validate(await read_json_body(request))
    await TableService().update_icon(baseId, tableId, body.icon)
    return Response(status_code=200)


@router.put("/{tableId}/description", status_code=200)
@permissions("table|update")
async def update_description(baseId: str, tableId: str, request: Request) -> Response:
    body = TableDescriptionBody.zod_validate(await read_json_body(request))
    await TableService().update_description(baseId, tableId, body.description)
    return Response(status_code=200)


@router.put("/{tableId}/db-table-name", status_code=200)
@permissions("table|update")
async def update_db_table_name(baseId: str, tableId: str, request: Request) -> Response:
    body = DbTableNameBody.zod_validate(await read_json_body(request))
    await TableService().update_db_table_name(baseId, tableId, body.dbTableName)
    return Response(status_code=200)


@router.put("/{tableId}/order", status_code=200)
@permissions("table|update")
async def update_order(baseId: str, tableId: str, request: Request) -> Response:
    body = UpdateOrderBody.zod_validate(await read_json_body(request))
    await TableService().update_order(baseId, tableId, body.anchorId, body.position)
    return Response(status_code=200)


@router.delete("/{tableId}", status_code=200)
@permissions("table|delete")
async def delete_table(baseId: str, tableId: str) -> Response:
    await TableService().delete_table(baseId, tableId)
    return Response(status_code=200)


@router.delete("/{tableId}/permanent", status_code=200)
@permissions("table|delete")
async def permanent_delete_table(baseId: str, tableId: str) -> Response:
    await TableService().permanent_delete_table(baseId, tableId)
    return Response(status_code=200)


@router.post("/{tableId}/duplicate", status_code=201)
@permissions("table|create", "table|read")
async def duplicate_table(baseId: str, tableId: str, request: Request) -> dict[str, Any]:
    body = DuplicateTableBody.zod_validate(await read_json_body(request))
    return await TableService().duplicate_table(baseId, tableId, body.name, body.includeRecords)


@router.get("/{tableId}/delete-references", status_code=200)
@permissions("table|read")
async def delete_references(tableId: str) -> dict[str, Any]:
    # link fields do not exist yet — nothing can reference a table.
    return {"dependentFields": []}


@router.get("/{tableId}/duplicate-check", status_code=200)
@permissions("table|read")
async def duplicate_check(tableId: str) -> dict[str, Any]:
    # cross-space link preview: empty until link fields land.
    return {"affectedFields": []}


@router.get("/{tableId}/field/{fieldId}/duplicate-check", status_code=200)
@permissions("field|create")
async def duplicate_field_check(tableId: str, fieldId: str) -> dict[str, Any]:
    # cross-space link preview: empty until link fields land.
    return {"affectedFields": []}


@router.get("/{tableId}/permission", status_code=200)
@permissions("table|read")
@allow_anonymous()
async def get_permission(tableId: str) -> dict[str, Any]:
    return await TableService().get_permission()


@router.post("/{tableId}/index", status_code=201)
@permissions("table|update")
async def toggle_index(baseId: str, tableId: str, request: Request) -> Response:
    body = ToggleIndexBody.zod_validate(await read_json_body(request))
    await TableIndexService().toggle_index(tableId, {"type": body.type})
    return Response(status_code=201)


@router.get("/{tableId}/activated-index", status_code=200)
@permissions("table|read")
async def get_activated_index(tableId: str) -> list[str]:
    return await TableIndexService().get_activated_indexes(tableId)


@router.get("/{tableId}/search-vector-status", status_code=200)
@permissions("table|read")
async def get_search_vector_status(tableId: str) -> dict[str, Any]:
    return await TableIndexService().get_search_vector_status(tableId)


@router.get("/{tableId}/abnormal-index", status_code=200)
@permissions("table|read")
async def get_abnormal_index(tableId: str, request: Request) -> list[dict[str, str]]:
    index_type = request.query_params.get("type")
    return await TableIndexService().get_abnormal_index(tableId, index_type)


@router.patch("/{tableId}/index/repair", status_code=200)
@permissions("table|update")
async def repair_index(tableId: str, request: Request) -> Response:
    index_type = request.query_params.get("type")
    await TableIndexService().repair_index(tableId, index_type)
    return Response(status_code=200)


@router.get("/{tableId}/default-view-id", status_code=200)
@permissions("table|read")
@allow_anonymous()
async def get_default_view_id(tableId: str) -> dict[str, str]:
    return await TableService().get_default_view_id(tableId)


@router.get("/{tableId}", status_code=200)
@permissions("table|read")
@allow_anonymous()
async def get_table(baseId: str, tableId: str) -> dict[str, Any]:
    return await TableService().get_table(baseId, tableId)
