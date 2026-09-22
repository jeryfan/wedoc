"""Routes for /api/base/:baseId/table.

Ports table-open-api.controller.ts. Index management, search-vector status
and socket doc-ids/snapshot-bulk are deferred (see docs/api-parity-ledger.md).
"""

from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from ...core.security.auth import auth_guard, permissions
from ...core.security.permissions import permission_guard
from ...core.validation import read_json_body
from .schemas import (
    CreateTableBody,
    DbTableNameBody,
    DuplicateTableBody,
    TableDescriptionBody,
    TableIconBody,
    TableNameBody,
    UpdateOrderBody,
)
from .service import TableService

router = APIRouter(
    prefix="/api/base/{baseId}/table",
    dependencies=[Depends(auth_guard), Depends(permission_guard)],
)


@router.post("", status_code=201)
@permissions("table|update")
async def create_table(baseId: str, request: Request) -> dict[str, Any]:
    body = CreateTableBody.zod_validate(await read_json_body(request))
    return await TableService().create_table(baseId, body)


@router.get("", status_code=200)
@permissions("table|read")
async def list_tables(baseId: str) -> list[dict[str, Any]]:
    return await TableService().list_tables(baseId)


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
async def get_permission(tableId: str) -> dict[str, Any]:
    return await TableService().get_permission()


@router.get("/{tableId}", status_code=200)
@permissions("table|read")
async def get_table(baseId: str, tableId: str) -> dict[str, Any]:
    return await TableService().get_table(baseId, tableId)
