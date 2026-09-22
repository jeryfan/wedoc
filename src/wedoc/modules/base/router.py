"""Routes for /api/base.

Ports base.controller.ts. Import/duplicate/template/export/connection/move/
erd/publish routes are not registered yet (out of scope for the single-PG
deployment; see docs/api-parity-ledger.md).
"""

from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from ...core.security.auth import (
    AllowAnonymousType,
    allow_anonymous,
    auth_guard,
    permissions,
    resource_meta,
)
from ...core.security.permissions import permission_guard
from ...core.validation import read_json_body
from ..collaborator.service import CollaboratorService
from ..invitation.service import RESOURCE_BASE, InvitationService
from ..space.schemas import DeleteCollaboratorQuery, UpdateCollaboratorBody
from .schemas import (
    AddBaseCollaboratorsBody,
    BaseInvitationLinkBody,
    CreateBaseBody,
    ListBaseCollaboratorQuery,
    ListBaseCollaboratorUserQuery,
    UpdateBaseBody,
    UpdateOrderBody,
)
from .service import BaseService

router = APIRouter(
    prefix="/api/base", dependencies=[Depends(auth_guard), Depends(permission_guard)]
)


def _empty() -> Response:
    return Response(status_code=200)


def _query_dict(request: Request) -> dict[str, Any]:
    params = dict(request.query_params)
    # zod array(): a single occurrence arrives as a plain string (rejected by
    # the schema); repeated keys arrive as an array.
    if "role" in params:
        roles = request.query_params.getlist("role")
        params["role"] = roles if len(roles) > 1 else roles[0]
    return params


@router.post("", status_code=201)
@permissions("base|create")
@resource_meta("spaceId", "body")
async def create_base(request: Request) -> dict[str, Any]:
    body = CreateBaseBody.zod_validate(await read_json_body(request))
    return await BaseService().create_base(body.spaceId, body.name, body.icon)


@router.get("/shared-base", status_code=200)
async def get_shared_base() -> list[dict[str, Any]]:
    return await CollaboratorService().get_shared_base()


@router.get("/access/all", status_code=200)
@permissions("base|read_all")
async def get_all_base() -> list[dict[str, Any]]:
    return await BaseService().get_all_base_list()


@router.get("/{baseId}", status_code=200)
@permissions("base|read")
@allow_anonymous(AllowAnonymousType.PUBLIC)
async def get_base(baseId: str) -> dict[str, Any]:
    return await BaseService().get_base_by_id(baseId)


@router.patch("/{baseId}", status_code=200)
@permissions("base|update")
async def update_base(baseId: str, request: Request) -> dict[str, Any]:
    raw = await read_json_body(request)
    body = UpdateBaseBody.zod_validate(raw)
    icon_set = isinstance(raw, dict) and "icon" in raw
    return await BaseService().update_base(baseId, body.name, body.icon, icon_set)


@router.put("/{baseId}/order", status_code=200)
@permissions("base|update")
async def update_order(baseId: str, request: Request) -> Response:
    body = UpdateOrderBody.zod_validate(await read_json_body(request))
    await BaseService().update_order(baseId, body.anchorId, body.position)
    return _empty()


@router.delete("/{baseId}", status_code=200)
@permissions("base|delete")
async def delete_base(baseId: str) -> Response:
    await BaseService().delete_base(baseId)
    return _empty()


@router.delete("/{baseId}/permanent", status_code=200)
async def permanent_delete_base(baseId: str) -> dict[str, Any]:
    await BaseService().permanent_delete_base(baseId)
    return {"baseId": baseId, "permanent": True}


@router.get("/{baseId}/permission", status_code=200)
@permissions("base|read")
@allow_anonymous(AllowAnonymousType.PUBLIC)
async def get_permission(baseId: str) -> dict[str, bool]:
    return await BaseService().get_permission()


@router.get("/{baseId}/collaborators", status_code=200)
@permissions("base|read")
async def list_collaborators(baseId: str, request: Request) -> dict[str, Any]:
    query = ListBaseCollaboratorQuery.zod_validate(_query_dict(request))
    service = CollaboratorService()
    collaborators = await service.get_list_by_base(baseId, query.model_dump())
    total = await service.get_total_base(baseId, query.model_dump())
    return {"collaborators": collaborators, "total": total}


@router.get("/{baseId}/collaborators/users", status_code=200)
@permissions("base|read")
async def list_collaborator_users(baseId: str, request: Request) -> dict[str, Any]:
    query = ListBaseCollaboratorUserQuery.zod_validate(dict(request.query_params))
    service = CollaboratorService()
    users = await service.get_user_collaborators(baseId, query.model_dump())
    total = await service.get_total_base(baseId, query.model_dump())
    return {"users": users, "total": total}


@router.post("/{baseId}/collaborator", status_code=201)
async def add_collaborators(baseId: str, request: Request) -> dict[str, int]:
    body = AddBaseCollaboratorsBody.zod_validate(await read_json_body(request))
    items = [
        {"principalId": c.principalId, "principalType": c.principalType} for c in body.collaborators
    ]
    return await CollaboratorService().add_base_collaborators(baseId, items, body.role)


@router.patch("/{baseId}/collaborators", status_code=200)
async def update_collaborator(baseId: str, request: Request) -> Response:
    body = UpdateCollaboratorBody.zod_validate(await read_json_body(request))
    await CollaboratorService().update_collaborator(
        baseId, "base", body.principalId, body.principalType, body.role
    )
    return _empty()


@router.delete("/{baseId}/collaborators", status_code=200)
async def delete_collaborator(baseId: str, request: Request) -> Response:
    query = DeleteCollaboratorQuery.zod_validate(dict(request.query_params))
    await CollaboratorService().delete_collaborator(
        baseId, "base", query.principalId, query.principalType
    )
    return _empty()


@router.post("/{baseId}/invitation/link", status_code=201)
@permissions("base|invite_link")
async def create_invitation_link(baseId: str, request: Request) -> dict[str, Any]:
    body = BaseInvitationLinkBody.zod_validate(await read_json_body(request))
    return await InvitationService().generate_invitation_link(
        baseId, body.role, RESOURCE_BASE
    )


@router.get("/{baseId}/invitation/link", status_code=200)
@permissions("base|invite_link")
async def list_invitation_links(baseId: str) -> list[dict[str, Any]]:
    return await InvitationService().list_invitation_links(baseId, RESOURCE_BASE)


@router.patch("/{baseId}/invitation/link/{invitationId}", status_code=200)
@permissions("base|invite_link")
async def update_invitation_link(
    baseId: str, invitationId: str, request: Request
) -> dict[str, Any]:
    body = BaseInvitationLinkBody.zod_validate(await read_json_body(request))
    return await InvitationService().update_invitation_link(
        baseId, invitationId, body.role, RESOURCE_BASE
    )


@router.delete("/{baseId}/invitation/link/{invitationId}", status_code=200)
@permissions("base|invite_link")
async def delete_invitation_link(baseId: str, invitationId: str) -> Response:
    await InvitationService().delete_invitation_link(baseId, invitationId, RESOURCE_BASE)
    return _empty()
