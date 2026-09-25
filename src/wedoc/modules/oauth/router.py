"""Routes for /api/oauth/client — ports oauth.controller.ts (client management)."""

from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from ...core import cls
from ...core.errors import ApiError, HttpErrorCode
from ...core.security.auth import auth_guard, token_access
from ...core.security.permissions import permission_guard
from ...core.validation import read_json_body
from .schemas import OAuthCreateRo, OAuthUpdateRo
from .service import OAuthService

router = APIRouter(
    prefix="/api/oauth/client",
    dependencies=[Depends(auth_guard), Depends(permission_guard)],
)


@router.get("/authorized/list", status_code=200)
async def get_authorized_list() -> list[dict[str, Any]]:
    return await OAuthService().get_authorized_list()


@router.get("", status_code=200)
async def get_oauth_list() -> list[dict[str, Any]]:
    return await OAuthService().get_oauth_list()


@router.post("", status_code=201)
async def create_oauth(request: Request) -> dict[str, Any]:
    ro = OAuthCreateRo.zod_validate(await read_json_body(request))
    return await OAuthService().create_oauth(ro)


@router.post("/{client_id}/secret", status_code=201)
async def generate_oauth_secret(client_id: str) -> dict[str, Any]:
    return await OAuthService().generate_secret(client_id)


@router.delete("/{client_id}/secret/{secret_id}", status_code=200)
async def delete_oauth_secret(client_id: str, secret_id: str) -> Response:
    await OAuthService().delete_secret(client_id, secret_id)
    return Response(status_code=200)


@router.post("/{client_id}/revoke-access", status_code=200)
async def revoke_access(client_id: str) -> Response:
    await OAuthService().revoke_access(client_id)
    return Response(status_code=200)


@router.post("/{client_id}/revoke-token", status_code=200)
async def revoke_token(client_id: str) -> Response:
    await OAuthService().revoke_token(client_id)
    return Response(status_code=200)


@router.get("/{client_id}/revoke-token", status_code=200)
@token_access()
async def revoke_token_get(client_id: str) -> Response:
    if not cls.get("accessTokenId"):
        raise ApiError(
            "only access token request can use this endpoint", HttpErrorCode.VALIDATION_ERROR
        )
    await OAuthService().revoke_token(client_id)
    return Response(status_code=200)


@router.get("/{client_id}", status_code=200)
async def get_oauth(client_id: str) -> dict[str, Any]:
    return await OAuthService().get_oauth(client_id)


@router.put("/{client_id}", status_code=200)
async def update_oauth(client_id: str, request: Request) -> dict[str, Any]:
    ro = OAuthUpdateRo.zod_validate(await read_json_body(request))
    return await OAuthService().update_oauth(client_id, ro)


@router.delete("/{client_id}", status_code=200)
async def delete_oauth(client_id: str) -> Response:
    await OAuthService().delete_oauth(client_id)
    return Response(status_code=200)
