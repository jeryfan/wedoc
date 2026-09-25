"""Routes for /api/access-token — ports access-token.controller.ts.

Session-authenticated; no @Permissions decorator upstream (owner scoping is
enforced in the service via the userId filter).
"""

from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from ...core.security.auth import auth_guard
from ...core.security.permissions import permission_guard
from ...core.validation import read_json_body
from .schemas import CreateAccessTokenRo, RefreshAccessTokenRo, UpdateAccessTokenRo
from .service import AccessTokenService

router = APIRouter(
    prefix="/api/access-token",
    dependencies=[Depends(auth_guard), Depends(permission_guard)],
)


@router.post("", status_code=201)
async def create_access_token(request: Request) -> dict[str, Any]:
    body = CreateAccessTokenRo.zod_validate(await read_json_body(request))
    return await AccessTokenService().create_access_token(body)


@router.put("/{access_token_id}", status_code=200)
async def update_access_token(access_token_id: str, request: Request) -> dict[str, Any]:
    body = UpdateAccessTokenRo.zod_validate(await read_json_body(request))
    return await AccessTokenService().update_access_token(access_token_id, body)


@router.delete("/{access_token_id}", status_code=200)
async def delete_access_token(access_token_id: str) -> Response:
    await AccessTokenService().delete_access_token(access_token_id)
    return Response(status_code=200)


@router.post("/{access_token_id}/refresh", status_code=200)
async def refresh_access_token(access_token_id: str, request: Request) -> dict[str, Any]:
    # Express body-parser yields {} for an absent body, so the .optional() outer
    # schema never sees undefined over HTTP — expiredTime is effectively required.
    body = RefreshAccessTokenRo.zod_validate(await read_json_body(request))
    return await AccessTokenService().refresh_access_token(access_token_id, body)


@router.get("", status_code=200)
async def list_access_tokens() -> list[dict[str, Any]]:
    return await AccessTokenService().list_access_token()


@router.get("/{access_token_id}", status_code=200)
async def get_access_token(access_token_id: str) -> dict[str, Any]:
    return await AccessTokenService().get_access_token(access_token_id)
