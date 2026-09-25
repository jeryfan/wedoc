"""Routes for /api/short-link — ports short-link.controller.ts.

POST is session-authenticated; GET /:code is @Public (redirect-time resolution).
"""

from typing import Any

from fastapi import APIRouter, Depends, Request

from ...core.security.auth import auth_guard
from ...core.security.permissions import permission_guard
from ...core.validation import read_json_body
from .schemas import CreateShortLinkRo
from .service import ShortLinkService

router = APIRouter(prefix="/api/short-link")


@router.post("", status_code=201, dependencies=[Depends(auth_guard), Depends(permission_guard)])
async def create_short_link(request: Request) -> dict[str, Any]:
    body = CreateShortLinkRo.zod_validate(await read_json_body(request))
    return await ShortLinkService().create_short_link(body)


@router.get("/{code}", status_code=200)
async def get_short_link(code: str) -> dict[str, Any]:
    return await ShortLinkService().get_short_link(code)
