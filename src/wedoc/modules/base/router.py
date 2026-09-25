"""Routes for /api/base.

Ports base.controller.ts. Import/duplicate/template/export/connection/move/
erd/publish routes are not registered yet (out of scope for the single-PG
deployment; see docs/api-parity-ledger.md).
"""

import json
from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from ...core.errors import ApiError
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
    BaseEmailInvitationBody,
    BaseInvitationLinkBody,
    CreateBaseBody,
    CreateFromTemplateBody,
    DuplicateBaseBody,
    ImportBaseBody,
    ListBaseCollaboratorQuery,
    ListBaseCollaboratorUserQuery,
    MoveBaseBody,
    PublishBaseBody,
    UpdateBaseBody,
    UpdateOrderBody,
)
from .service import BaseService

_DUP_SSE_HEADERS = {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _dup_sse(events: list[dict[str, Any]]) -> Response:
    body = "".join(
        f"data: {json.dumps(e, ensure_ascii=False, separators=(',', ':'))}\n\n"
        for e in events
    )
    return Response(
        content=body,
        media_type="text/event-stream",
        headers=_DUP_SSE_HEADERS,
        status_code=201,
    )


def _sse(events: list[dict[str, Any]], status_code: int = 200) -> Response:
    body = "".join(
        f"data: {json.dumps(e, ensure_ascii=False, separators=(',', ':'))}\n\n"
        for e in events
    )
    return Response(
        content=body,
        media_type="text/event-stream",
        headers=_DUP_SSE_HEADERS,
        status_code=status_code,
    )

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


@router.post("/duplicate", status_code=201)
@permissions("base|create")
@resource_meta("spaceId", "body")
async def duplicate_base(request: Request) -> dict[str, Any]:
    body = DuplicateBaseBody.zod_validate(await read_json_body(request))
    return await BaseService().duplicate_base(
        body.fromBaseId, body.spaceId, bool(body.withRecords), body.name
    )


@router.post("/duplicate-stream", status_code=201)
@permissions("base|create")
@resource_meta("spaceId", "body")
async def duplicate_base_stream(request: Request) -> Response:
    body = DuplicateBaseBody.zod_validate(await read_json_body(request))
    events: list[dict[str, Any]] = [{"type": "progress", "phase": "duplicate_started"}]
    try:
        base = await BaseService().duplicate_base(
            body.fromBaseId, body.spaceId, bool(body.withRecords), body.name
        )
        events.append({"type": "done", "data": base})
    except ApiError as exc:
        events.append({"type": "error", "message": exc.message})
    return _dup_sse(events)


@router.post("/create-from-template", status_code=201)
@permissions("base|create")
@resource_meta("spaceId", "body")
async def create_base_from_template(request: Request) -> dict[str, Any]:
    body = CreateFromTemplateBody.zod_validate(await read_json_body(request))
    return await BaseService().create_base_from_template(
        body.spaceId, body.templateId, bool(body.withRecords), body.baseId
    )


@router.post("/import", status_code=201)
@permissions("base|create")
@resource_meta("spaceId", "body")
async def import_base(request: Request) -> dict[str, Any]:
    from .import_export import import_base as _import_base

    body = ImportBaseBody.zod_validate(await read_json_body(request))
    return await _import_base(body.notify.model_dump(), body.spaceId)


@router.post("/import-stream", status_code=200)
@permissions("base|create")
@resource_meta("spaceId", "body")
async def import_base_stream(request: Request) -> Response:
    from .import_export import import_base as _import_base

    body = ImportBaseBody.zod_validate(await read_json_body(request))
    events: list[dict[str, Any]] = [{"type": "progress", "phase": "parsing_structure"}]
    try:
        result = await _import_base(body.notify.model_dump(), body.spaceId)
        events.append({"type": "done", "data": result})
    except ApiError as exc:
        events.append({"type": "error", "message": exc.message})
    return _sse(events)


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


@router.get("/{baseId}/duplicate-check", status_code=200)
@permissions("base|read")
async def duplicate_base_check(baseId: str, request: Request) -> dict[str, Any]:
    dest_space_id = request.query_params.get("destSpaceId")
    return await BaseService().duplicate_base_check(baseId, dest_space_id)


def _include_data(request: Request) -> bool:
    raw = request.query_params.get("includeData")
    if raw is None:
        return True
    return raw.lower() not in ("false", "0")


@router.get("/{baseId}/export", status_code=200)
@permissions("base|read")
async def export_base(baseId: str, request: Request) -> dict[str, Any]:
    from .import_export import export_base as _export_base

    return await _export_base(baseId, _include_data(request))


@router.get("/{baseId}/export-stream", status_code=200)
@permissions("base|read")
async def export_base_stream(baseId: str, request: Request) -> Response:
    from .import_export import export_base as _export_base

    events: list[dict[str, Any]] = [{"type": "progress", "phase": "preparing"}]
    try:
        result = await _export_base(baseId, _include_data(request))
        events.append({"type": "done", "data": result})
    except ApiError as exc:
        events.append({"type": "error", "message": exc.message})
    return _sse(events)


@router.get("/{baseId}/erd", status_code=200)
@permissions("base|update")
async def generate_base_erd(baseId: str) -> dict[str, Any]:
    return await BaseService().generate_base_erd(baseId)


@router.post("/{baseId}/publish", status_code=201)
@permissions("base|update")
async def publish_base(baseId: str, request: Request) -> dict[str, Any]:
    body = PublishBaseBody.zod_validate(await read_json_body(request))
    return await BaseService().publish_base(baseId, body)


@router.put("/{baseId}/move", status_code=200)
@permissions("space|update")
async def move_base(baseId: str, request: Request) -> dict[str, Any]:
    body = MoveBaseBody.zod_validate(await read_json_body(request))
    return await BaseService().move_base(baseId, body.spaceId)


@router.get("/{baseId}/move-check", status_code=200)
@permissions("space|update")
async def move_base_check(baseId: str, request: Request) -> dict[str, Any]:
    space_id = request.query_params.get("spaceId")
    return await BaseService().check_move_base(baseId, space_id)


@router.get("/{baseId}/move-job/{jobId}", status_code=200)
@permissions("space|update")
async def get_base_move_job(baseId: str, jobId: str) -> dict[str, Any]:
    return await BaseService().get_move_job(baseId, jobId)


@router.post("/{baseId}/move-job/{jobId}/cancel", status_code=201)
@permissions("space|update")
async def cancel_base_move_job(baseId: str, jobId: str) -> dict[str, Any]:
    return await BaseService().get_move_job(baseId, jobId)


@router.post("/{baseId}/move-job/{jobId}/retry", status_code=201)
@permissions("space|update")
async def retry_base_move_job(baseId: str, jobId: str) -> dict[str, Any]:
    return await BaseService().get_move_job(baseId, jobId)


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


@router.post("/{baseId}/invitation/email", status_code=201)
@permissions("base|invite_email")
async def email_invitation(baseId: str, request: Request) -> dict[str, Any]:
    body = BaseEmailInvitationBody.zod_validate(await read_json_body(request))
    return await InvitationService().email_invitation(
        baseId, body.emails, body.role, RESOURCE_BASE
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


@router.post("/{baseId}/connection", status_code=201)
@permissions("base|db_connection")
async def create_db_connection(baseId: str) -> Any:
    from .db_connection import DbConnectionService

    result = await DbConnectionService().create(baseId)
    return result if result is not None else Response(status_code=201)


@router.get("/{baseId}/connection", status_code=200)
@permissions("base|db_connection")
async def get_db_connection(baseId: str) -> Any:
    from .db_connection import DbConnectionService

    result = await DbConnectionService().retrieve(baseId)
    return result if result is not None else Response(status_code=200)


@router.delete("/{baseId}/connection", status_code=200)
@permissions("base|db_connection")
async def delete_db_connection(baseId: str) -> Response:
    from .db_connection import DbConnectionService

    await DbConnectionService().remove(baseId)
    return _empty()
