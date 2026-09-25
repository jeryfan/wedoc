"""Routes for /api/pin — ports pin.controller.ts.

Session-authenticated; owner scoping via createdBy in the service.
"""

from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from ...core.security.auth import auth_guard
from ...core.security.permissions import permission_guard
from ...core.validation import read_json_body
from .schemas import AddPinRo, DeletePinRo, UpdatePinOrderRo
from .service import PinService

router = APIRouter(prefix="/api/pin", dependencies=[Depends(auth_guard), Depends(permission_guard)])


@router.post("", status_code=201)
async def add_pin(request: Request) -> dict[str, Any]:
    body = AddPinRo.zod_validate(await read_json_body(request))
    return await PinService().add_pin(body)


@router.delete("", status_code=200)
async def delete_pin(request: Request) -> dict[str, Any]:
    params = request.query_params
    raw = {k: params[k] for k in ("type", "id") if k in params}
    query = DeletePinRo.zod_validate(raw)
    return await PinService().delete_pin(query)


@router.get("/list", status_code=200)
async def get_list() -> list[dict[str, Any]]:
    return await PinService().get_list()


@router.get("/entry-map", status_code=200)
async def get_entry_map() -> dict[str, str]:
    return await PinService().get_entry_map()


@router.put("/order", status_code=200)
async def update_order(request: Request) -> Response:
    body = UpdatePinOrderRo.zod_validate(await read_json_body(request))
    await PinService().update_order(body)
    return Response(status_code=200)