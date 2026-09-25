"""Routes for /api/integrity — ports integrity.controller.ts.

wedoc has no link fields yet, so the link-integrity check has nothing to
inspect and returns an empty, issue-free result (matching ref on a base with
no link fields). The base|update guard resolves and gates the base.
"""

from typing import Any

from fastapi import APIRouter, Depends

from ...core.security.auth import auth_guard, permissions
from ...core.security.permissions import permission_guard

router = APIRouter(
    prefix="/api/integrity",
    dependencies=[Depends(auth_guard), Depends(permission_guard)],
)


@router.get("/base/{baseId}/link-check", status_code=200)
@permissions("base|update")
async def check_base_integrity(baseId: str) -> dict[str, Any]:
    return {"hasIssues": False, "linkFieldIssues": []}


@router.post("/base/{baseId}/link-fix", status_code=201)
@permissions("base|update")
async def fix_base_integrity(baseId: str) -> list[Any]:
    return []
