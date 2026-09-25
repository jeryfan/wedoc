"""Routes for /api/space.

Ports space.controller.ts. BYODB data-db, integration management, test-llm
and email invitation are not registered yet (out of scope for the single-PG
deployment; see docs/api-parity-ledger.md).
"""

from typing import Any

from fastapi import APIRouter, Depends, Request, Response, UploadFile

from ...core.security.auth import auth_guard, permissions
from ...core.security.permissions import permission_guard
from ...core.validation import read_json_body
from ..collaborator.service import CollaboratorService
from ..invitation.service import InvitationService
from .schemas import (
    AddCollaboratorsBody,
    BaseEntryMapQuery,
    CreateIntegrationRo,
    CreateSpaceBody,
    DeleteCollaboratorQuery,
    EmailInvitationBody,
    InvitationLinkBody,
    ListCollaboratorQuery,
    SpaceSearchQuery,
    UpdateCollaboratorBody,
    UpdateIntegrationRo,
)
from .service import SpaceService

router = APIRouter(
    prefix="/api/space", dependencies=[Depends(auth_guard), Depends(permission_guard)]
)


def _empty() -> Response:
    return Response(status_code=200)


@router.get("", status_code=200)
@permissions("space|read")
async def get_space_list() -> list[dict[str, Any]]:
    return await SpaceService().get_space_list()


@router.post("", status_code=201)
@permissions("space|create")
async def create_space(request: Request) -> dict[str, Any]:
    raw = await read_json_body(request)
    body = CreateSpaceBody.zod_validate(raw)
    data_db = raw.get("dataDb") if isinstance(raw, dict) else None
    return await SpaceService().create_space(body.name, data_db)


@router.get("/{spaceId}/base", status_code=200)
@permissions("base|read")
async def get_base_list(spaceId: str) -> list[dict[str, Any]]:
    return await SpaceService().get_base_list_by_space_id(spaceId)


@router.get("/{spaceId}/base-entry-map", status_code=200)
@permissions("base|read")
async def get_base_entry_map(spaceId: str, request: Request) -> dict[str, str]:
    query = BaseEntryMapQuery.zod_validate(dict(request.query_params))
    return await SpaceService().get_base_entry_map(spaceId, query.take)


@router.get("/{spaceId}/search", status_code=200)
@permissions("space|read")
async def search_space(spaceId: str, request: Request) -> dict[str, Any]:
    query = SpaceSearchQuery.zod_validate(dict(request.query_params))
    return await SpaceService().search(spaceId, query.model_dump())


@router.post("/{spaceId}/invitation/link", status_code=201)
@permissions("space|invite_link")
async def create_invitation_link(spaceId: str, request: Request) -> dict[str, Any]:
    body = InvitationLinkBody.zod_validate(await read_json_body(request))
    return await InvitationService().generate_invitation_link(spaceId, body.role)


@router.post("/{spaceId}/invitation/email", status_code=201)
@permissions("space|invite_email")
async def email_invitation(spaceId: str, request: Request) -> dict[str, Any]:
    body = EmailInvitationBody.zod_validate(await read_json_body(request))
    return await InvitationService().email_invitation(spaceId, body.emails, body.role)


@router.get("/{spaceId}/invitation/link", status_code=200)
@permissions("space|invite_link")
async def list_invitation_links(spaceId: str) -> list[dict[str, Any]]:
    return await InvitationService().list_invitation_links(spaceId)


@router.patch("/{spaceId}/invitation/link/{invitationId}", status_code=200)
@permissions("space|invite_link")
async def update_invitation_link(
    spaceId: str, invitationId: str, request: Request
) -> dict[str, Any]:
    body = InvitationLinkBody.zod_validate(await read_json_body(request))
    return await InvitationService().update_invitation_link(spaceId, invitationId, body.role)


@router.delete("/{spaceId}/invitation/link/{invitationId}", status_code=200)
@permissions("space|invite_link")
async def delete_invitation_link(spaceId: str, invitationId: str) -> Response:
    await InvitationService().delete_invitation_link(spaceId, invitationId)
    return _empty()


@router.get("/{spaceId}/collaborators/unique", status_code=200)
@permissions("space|read")
async def list_unique_collaborators(spaceId: str, request: Request) -> dict[str, Any]:
    query = ListCollaboratorQuery.zod_validate(dict(request.query_params))
    return await CollaboratorService().get_unique_list_by_space(spaceId, query.model_dump())


@router.get("/{spaceId}/collaborators", status_code=200)
@permissions("space|read")
async def list_collaborators(spaceId: str, request: Request) -> dict[str, Any]:
    query = ListCollaboratorQuery.zod_validate(dict(request.query_params))
    service = CollaboratorService()
    stats = await service.get_space_collaborator_stats(spaceId, query.model_dump())
    collaborators = await service.get_list_by_space(spaceId, query.model_dump())
    return {
        "collaborators": collaborators,
        "total": stats["total"],
        "uniqTotal": stats["uniqTotal"],
    }


@router.post("/{spaceId}/collaborator", status_code=201)
@permissions("space|read")
async def add_collaborators(spaceId: str, request: Request) -> Response:
    body = AddCollaboratorsBody.zod_validate(await read_json_body(request))
    items = [
        {"principalId": c.principalId, "principalType": c.principalType} for c in body.collaborators
    ]
    await CollaboratorService().add_space_collaborators(spaceId, items, body.role)
    return Response(status_code=201)


@router.patch("/{spaceId}/collaborators", status_code=200)
@permissions("space|read")
async def update_collaborator(spaceId: str, request: Request) -> Response:
    body = UpdateCollaboratorBody.zod_validate(await read_json_body(request))
    service = CollaboratorService()
    if body.role != "owner" and await service.is_unique_owner_user(spaceId, body.principalId):
        from ...core.errors import ApiError, HttpErrorCode

        raise ApiError(
            "Cannot change the role of the only owner of the space",
            HttpErrorCode.VALIDATION_ERROR,
            {"localization": {"i18nKey": "httpErrors.space.cannotChangeOnlyOwnerRole"}},
        )
    await service.update_collaborator(
        spaceId, "space", body.principalId, body.principalType, body.role
    )
    return _empty()


@router.delete("/{spaceId}/collaborators", status_code=200)
@permissions("space|read")
async def delete_collaborator(spaceId: str, request: Request) -> dict[str, Any] | None:
    query = DeleteCollaboratorQuery.zod_validate(dict(request.query_params))
    service = CollaboratorService()
    if await service.is_unique_owner_user(spaceId, query.principalId):
        from ...core.errors import ApiError, HttpErrorCode

        raise ApiError(
            "Cannot delete the only owner of the space",
            HttpErrorCode.VALIDATION_ERROR,
            {"localization": {"i18nKey": "httpErrors.space.cannotDeleteOnlyOwner"}},
        )
    return await service.delete_collaborator(
        spaceId, "space", query.principalId, query.principalType
    )


@router.delete("/{spaceId}/collaborators/base", status_code=200)
@permissions("space|read")
async def delete_base_collaborators(spaceId: str, request: Request) -> Response:
    query = DeleteCollaboratorQuery.zod_validate(dict(request.query_params))
    await CollaboratorService().delete_base_collaborators_by_space(
        spaceId, query.principalId, query.principalType
    )
    return _empty()


@router.patch("/{spaceId}/avatar", status_code=200)
@permissions("space|update")
async def update_space_avatar(spaceId: str, file: UploadFile | None = None) -> Response:
    data = await file.read() if file is not None else None
    await SpaceService().update_space_avatar(spaceId, data)
    return _empty()


@router.get("/{spaceId}", status_code=200)
@permissions("space|read")
async def get_space(spaceId: str) -> dict[str, Any]:
    return await SpaceService().get_space_by_id(spaceId)


@router.patch("/{spaceId}", status_code=200)
@permissions("space|update")
async def update_space(spaceId: str, request: Request) -> dict[str, Any]:
    body = CreateSpaceBody.zod_validate(await read_json_body(request))
    return await SpaceService().update_space(spaceId, body.name)


@router.delete("/{spaceId}", status_code=200)
@permissions("space|delete")
async def delete_space(spaceId: str) -> Response:
    await SpaceService().delete_space(spaceId)
    return _empty()


@router.delete("/{spaceId}/permanent", status_code=200)
async def permanent_delete_space(spaceId: str) -> dict[str, Any]:
    return await SpaceService().permanent_delete_space(spaceId)


# --- AI integration management (ports space.controller integration routes) ---


@router.get("/{spaceId}/integration", status_code=200)
@permissions("space|update")
async def get_integration_list(spaceId: str) -> list[dict[str, Any]]:
    return await SpaceService().get_integration_list(spaceId)


@router.post("/{spaceId}/integration", status_code=201)
@permissions("space|update")
async def create_integration(spaceId: str, request: Request) -> dict[str, Any]:
    ro = CreateIntegrationRo.zod_validate(await read_json_body(request))
    return await SpaceService().create_integration(spaceId, ro)


@router.patch("/{spaceId}/integration/{integrationId}", status_code=200)
@permissions("space|update")
async def update_integration(
    spaceId: str, integrationId: str, request: Request
) -> dict[str, Any]:
    ro = UpdateIntegrationRo.zod_validate(await read_json_body(request))
    return await SpaceService().update_integration(integrationId, ro, spaceId)


@router.delete("/{spaceId}/integration/{integrationId}", status_code=200)
@permissions("space|update")
async def delete_integration(spaceId: str, integrationId: str) -> Response:
    await SpaceService().delete_integration(integrationId, spaceId)
    return _empty()


@router.post("/{spaceId}/test-llm", status_code=201)
@permissions("space|update")
async def test_integration_llm(spaceId: str, request: Request) -> dict[str, Any]:
    from ..setting.schemas import TestLLMRo

    ro = TestLLMRo.zod_validate(await read_json_body(request))
    return await SpaceService().test_integration_llm(ro)


# --- BYODB space data database (ports space.controller data-db routes) -------


@router.post("/data-db/preflight", status_code=201)
@permissions("space|create")
async def preflight_data_db(request: Request) -> dict[str, Any]:
    from .data_db import DataDbService, admin_only_error
    from .schemas import DataDbPreflightRo

    ro = DataDbPreflightRo.zod_validate(await read_json_body(request))
    if ro.targetMode == "migrate-space":
        raise admin_only_error()
    return await DataDbService().preflight(ro)


@router.get("/{spaceId}/data-db", status_code=200)
@permissions("space|read")
async def get_space_data_db(spaceId: str, request: Request) -> dict[str, Any]:
    from .data_db import DataDbSummaryService

    include = request.query_params.get("includeRelatedSpaces") != "false"
    return await DataDbSummaryService().get_summary(spaceId, include)


@router.patch("/{spaceId}/data-db", status_code=200)
@permissions("space|update")
async def update_space_data_db(spaceId: str, request: Request) -> dict[str, Any]:
    from .data_db import admin_only_error
    from .schemas import DataDbPreflightRo

    DataDbPreflightRo.zod_validate(await read_json_body(request))
    raise admin_only_error()


@router.post("/{spaceId}/data-db/retest", status_code=201)
@permissions("space|update")
async def retest_space_data_db(spaceId: str) -> dict[str, Any]:
    from .data_db import binding_not_found_error

    raise binding_not_found_error()


@router.post("/{spaceId}/data-db/retry", status_code=201)
@permissions("space|update")
async def retry_space_data_db(spaceId: str) -> dict[str, Any]:
    from .data_db import binding_not_found_error

    raise binding_not_found_error()


@router.get("/{spaceId}/data-db/migration/{jobId}", status_code=200)
@permissions("space|read")
async def get_space_data_db_migration(spaceId: str, jobId: str) -> dict[str, Any]:
    from .data_db import admin_only_error

    raise admin_only_error()


@router.post("/{spaceId}/data-db/migration/{jobId}/cancel", status_code=201)
@permissions("space|update")
async def cancel_space_data_db_migration(spaceId: str, jobId: str) -> dict[str, Any]:
    from .data_db import admin_only_error

    raise admin_only_error()


@router.post("/{spaceId}/data-db/migration/{jobId}/rollback", status_code=201)
@permissions("space|update")
async def rollback_space_data_db_migration(spaceId: str, jobId: str) -> dict[str, Any]:
    from .data_db import admin_only_error

    raise admin_only_error()
