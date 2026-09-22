"""Routes for /api/invitation.

Ports invitation.controller.ts. The route carries no @Permissions decorator
upstream — any authenticated user may accept an invitation link.
"""

from typing import Any

from fastapi import APIRouter, Depends, Request

from ...core.security.auth import auth_guard
from ...core.security.permissions import permission_guard
from ...core.validation import read_json_body
from .schemas import AcceptInvitationLinkBody
from .service import InvitationService

router = APIRouter(
    prefix="/api/invitation", dependencies=[Depends(auth_guard), Depends(permission_guard)]
)


@router.post("/link/accept", status_code=201)
async def accept_link(request: Request) -> dict[str, Any]:
    body = AcceptInvitationLinkBody.zod_validate(await read_json_body(request))
    return await InvitationService().accept_invitation_link(body)
