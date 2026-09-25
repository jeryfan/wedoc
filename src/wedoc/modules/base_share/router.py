"""Routes for base-share.

Management routes: /api/base/:baseId/share (auth + base permissions).
Public routes: /api/share/:shareId/base* (@Public share auth).
"""

from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from ...core.security.auth import auth_guard, permissions, resource_meta
from ...core.security.permissions import permission_guard
from ...core.validation import read_json_body
from .schemas import CopyBaseShareRo, CreateBaseShareRo, UpdateBaseShareRo
from .service import BaseShareService

router = APIRouter(
    prefix="/api/base/{baseId}/share",
    dependencies=[Depends(auth_guard), Depends(permission_guard)],
)


@router.post("", status_code=201)
@permissions("base|update")
async def create(baseId: str, request: Request) -> dict[str, Any]:
    body = CreateBaseShareRo.zod_validate(await read_json_body(request))
    return await BaseShareService().create_base_share(baseId, body.nodeId)


@router.get("", status_code=200)
@permissions("base|read")
async def list_shares(baseId: str) -> list[dict[str, Any]]:
    return await BaseShareService().get_base_share_list(baseId)


@router.get("/node", status_code=200)
@permissions("base|read")
async def get_base_share(baseId: str) -> Any:
    result = await BaseShareService().get_base_share(baseId)
    if result is None:
        return Response(status_code=200)
    return result


@router.get("/node/{nodeId}", status_code=200)
@permissions("base|read")
async def get_by_node_id(baseId: str, nodeId: str) -> Any:
    result = await BaseShareService().get_base_share_by_node_id(baseId, nodeId)
    if result is None:
        return Response(status_code=200)
    return result


@router.patch("/{shareId}", status_code=200)
@permissions("base|update")
async def update(baseId: str, shareId: str, request: Request) -> dict[str, Any]:
    body = UpdateBaseShareRo.zod_validate(await read_json_body(request))
    data = body.model_dump(exclude_unset=True)
    return await BaseShareService().update_base_share(baseId, shareId, data)


@router.delete("/{shareId}", status_code=200)
@permissions("base|update")
async def delete(baseId: str, shareId: str) -> Response:
    await BaseShareService().delete_base_share(baseId, shareId)
    return Response(status_code=200)


@router.post("/{shareId}/refresh", status_code=201)
@permissions("base|update")
async def refresh(baseId: str, shareId: str) -> dict[str, Any]:
    return await BaseShareService().refresh_base_share_id(baseId, shareId)


# -- public share routes -------------------------------------------------------

open_router = APIRouter(prefix="/api/share")


@open_router.post("/{shareId}/base/auth", status_code=200)
async def base_share_auth(shareId: str, request: Request) -> Response:
    body = await read_json_body(request)
    password = body.get("password") if isinstance(body, dict) else None
    service = BaseShareService()
    auth_share_id = await service.auth_base_share(shareId, password)
    if not auth_share_id:
        from ...core.errors import ApiError, HttpErrorCode

        raise ApiError(
            "Incorrect password.",
            HttpErrorCode.VALIDATION_ERROR,
            {"localization": {"i18nKey": "httpErrors.share.incorrectPassword"}},
        )
    token = service.auth_token(shareId, password)
    response = JSONResponse(content={"token": token}, status_code=200)
    response.set_cookie(
        key=shareId,
        value=token,
        max_age=60 * 60 * 24 * 7,
        httponly=True,
        samesite="lax",
    )
    return response


@open_router.get("/{shareId}/base", status_code=200)
async def get_base_share_open(shareId: str, request: Request) -> dict[str, Any]:
    jwt_cookie = request.cookies.get(shareId)
    return await BaseShareService().get_base_share_open(shareId, jwt_cookie)


@open_router.post(
    "/{shareId}/base/copy",
    status_code=200,
    dependencies=[Depends(auth_guard), Depends(permission_guard)],
)
@permissions("base|create")
@resource_meta("spaceId", "body")
async def copy_base_share(shareId: str, request: Request) -> dict[str, Any]:
    body = CopyBaseShareRo.zod_validate(await read_json_body(request))
    jwt_cookie = request.cookies.get(shareId)
    return await BaseShareService().copy_base_share(
        shareId, jwt_cookie, body.spaceId, body.name, body.withRecords, body.baseId
    )
