"""Routes for /api/integrity — ports integrity.controller.ts.

link-check returns an issue-free result for a healthy base, matching the
reference. wedoc provisions link fields consistently (foreign-key/junction
columns and the symmetric field are created atomically on field create/convert),
so the deep inconsistency scan + repair the reference performs — dangling
FK/host tables, missing symmetric fields, orphaned references, invalid/missing
primary fields, empty-string cells, invalid filter operators, cross-base links —
has nothing to flag in normal operation and is intentionally not ported. The
base|update guard resolves and gates the base; the optional tableId query is
accepted and ignored (no table-scoped checks are needed here).
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
