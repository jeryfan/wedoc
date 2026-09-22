"""Routes for /api/base/:baseId/table.

Ports table-open-api.controller.ts. Index management, search-vector status
and socket doc-ids/snapshot-bulk are deferred (see docs/api-parity-ledger.md).
"""

from typing import Any

from fastapi import APIRouter, Depends, Request

from ...core.security.auth import auth_guard, permissions
from ...core.security.permissions import permission_guard
from ...core.validation import read_json_body
from .schemas import CreateTableBody
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


@router.get("/{tableId}", status_code=200)
@permissions("table|read")
async def get_table(baseId: str, tableId: str) -> dict[str, Any]:
    return await TableService().get_table(baseId, tableId)
