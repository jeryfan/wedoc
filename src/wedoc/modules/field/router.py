"""Routes for /api/table/:tableId/field.

Ports field-open-api.controller.ts. convert + plan routes and the socket
snapshot/doc-ids endpoints are deferred (see docs/api-parity-ledger.md);
filter-link-records stays unimplemented until link fields land.
"""

from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from ...core.security.auth import auth_guard, permissions
from ...core.security.permissions import permission_guard
from ...core.validation import read_json_body
from .schemas import DuplicateFieldBody, FieldConvertBody, FieldCreateBody, FieldPatchBody
from .service import FieldService

router = APIRouter(
    prefix="/api/table/{tableId}/field",
    dependencies=[Depends(auth_guard), Depends(permission_guard)],
)


@router.get("/delete-references", status_code=200)
@permissions("field|delete")
async def delete_references(tableId: str, request: Request) -> dict[str, Any]:
    return await FieldService().delete_references(tableId, request.query_params.getlist("fieldIds"))


@router.get("/{fieldId}", status_code=200)
@permissions("field|read")
async def get_field(tableId: str, fieldId: str) -> dict[str, Any]:
    return await FieldService().get_field(tableId, fieldId)


@router.get("", status_code=200)
@permissions("field|read")
async def list_fields(tableId: str, request: Request) -> list[dict[str, Any]]:
    projection = request.query_params.getlist("projection")
    return await FieldService().list_fields(tableId, projection or None)


@router.post("", status_code=201)
@permissions("field|create")
async def create_field(tableId: str, request: Request) -> dict[str, Any]:
    body = FieldCreateBody.zod_validate(await read_json_body(request))
    return await FieldService().create_field(tableId, body)


@router.get("/{fieldId}/plan", status_code=200)
@permissions("field|read")
async def plan_delete_field(tableId: str, fieldId: str) -> dict[str, Any]:
    return await FieldService().plan_delete(tableId, fieldId)


@router.delete("/{fieldId}/plan", status_code=200)
@permissions("field|delete")
async def plan_delete_field_confirm(tableId: str, fieldId: str) -> dict[str, Any]:
    plan = await FieldService().plan_delete(tableId, fieldId)
    plan["linkFieldCount"] = 0
    return plan


@router.put("/{fieldId}/plan", status_code=200)
@permissions("field|update")
async def plan_convert_field(tableId: str, fieldId: str, request: Request) -> dict[str, Any]:
    body = FieldConvertBody.zod_validate(await read_json_body(request))
    return await FieldService().plan_convert(tableId, fieldId, body)


@router.put("/{fieldId}/convert", status_code=200)
@permissions("field|update")
async def convert_field(tableId: str, fieldId: str, request: Request) -> dict[str, Any]:
    body = FieldConvertBody.zod_validate(await read_json_body(request))
    return await FieldService().convert_field(tableId, fieldId, body)


@router.post("/plan", status_code=201)
@permissions("field|create")
async def plan_create_field(tableId: str, request: Request) -> dict[str, Any]:
    body = FieldCreateBody.zod_validate(await read_json_body(request))
    return await FieldService().plan_create(tableId, body)


@router.patch("/{fieldId}", status_code=200)
@permissions("field|update")
async def update_field(tableId: str, fieldId: str, request: Request) -> dict[str, Any]:
    body = FieldPatchBody.zod_validate(await read_json_body(request))
    return await FieldService().update_field(tableId, fieldId, body)


@router.delete("/{fieldId}", status_code=200)
@permissions("field|delete")
async def delete_field(tableId: str, fieldId: str) -> Response:
    await FieldService().delete_field(tableId, fieldId)
    return Response(status_code=200)


@router.delete("", status_code=200)
@permissions("field|delete")
async def delete_fields(tableId: str, request: Request) -> Response:
    await FieldService().delete_fields(tableId, request.query_params.getlist("fieldIds"))
    return Response(status_code=200)


@router.post("/{fieldId}/duplicate", status_code=201)
@permissions("field|create")
async def duplicate_field(tableId: str, fieldId: str, request: Request) -> dict[str, Any]:
    body = DuplicateFieldBody.zod_validate(await read_json_body(request))
    return await FieldService().duplicate_field(tableId, fieldId, body)


@router.post("/{fieldId}/auto-fill", status_code=201)
@permissions("record|update")
async def auto_fill_field(tableId: str, fieldId: str) -> dict[str, Any]:
    return {"taskId": None}


@router.post("/{fieldId}/stop-fill", status_code=201)
@permissions("record|update")
async def stop_fill_field(tableId: str, fieldId: str) -> Response:
    return Response(status_code=201)
