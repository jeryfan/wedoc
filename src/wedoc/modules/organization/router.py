"""Routes for /api/organization — ports organization.controller.ts.

The community edition ships stub responses; organization/department data is an
enterprise feature. Session-authenticated (no @Public upstream).
"""

from typing import Any

from fastapi import APIRouter, Depends, Response

from ...core.security.auth import auth_guard
from ...core.security.permissions import permission_guard

router = APIRouter(
    prefix="/api/organization",
    dependencies=[Depends(auth_guard), Depends(permission_guard)],
)


@router.get("/me", status_code=200)
async def get_organization_me() -> Response:
    # upstream returns null -> Nest sends an empty body
    return Response(status_code=200)


@router.get("/department-user", status_code=200)
async def get_department_users() -> dict[str, Any]:
    return {"users": [], "total": 0}


@router.get("/department", status_code=200)
async def get_department_list() -> list[Any]:
    return []
