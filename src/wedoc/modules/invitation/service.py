"""Invitation domain service — ports features/invitation/invitation.service.ts.

Link-based invitations only; email invitation lands with the mailer-driven
flows later.
"""

import hashlib
import hmac
from datetime import UTC, datetime
from typing import Any

from ...config import get_settings
from ...core import cls
from ...core.errors import ApiError, HttpErrorCode
from ...core.ids import IdPrefix, cuid, new_id
from ..collaborator.service import CollaboratorService
from ..space import repository

RESOURCE_SPACE = "space"
RESOURCE_BASE = "base"


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def generate_invitation_code(invitation_id: str) -> str:
    """code-generate.ts: HMAC-SHA256(secretKey, invitationId) hex."""
    secret = get_settings().resolved_secret_key
    return hmac.new(secret.encode(), invitation_id.encode(), hashlib.sha256).hexdigest()


class InvitationService:
    def __init__(self) -> None:
        self.collaborators = CollaboratorService()

    @staticmethod
    def _invite_url(invitation_id: str, code: str) -> str:
        origin = get_settings().public_origin
        return f"{origin}/invite?invitationId={invitation_id}&invitationCode={code}"

    async def generate_invitation_link(
        self, resource_id: str, role: str, resource_type: str = RESOURCE_SPACE
    ) -> dict[str, Any]:
        await self.collaborators.validate_user_add_role(
            cls.get("user.id"), role, resource_id, resource_type
        )
        invitation_id = new_id(IdPrefix.INVITATION)
        code = generate_invitation_code(invitation_id)
        row = await repository.insert_invitation(
            {
                "id": invitation_id,
                "invitation_code": code,
                "space_id": resource_id if resource_type == RESOURCE_SPACE else None,
                "base_id": resource_id if resource_type == RESOURCE_BASE else None,
                "role": role,
                "type": "link",
                "expired_time": None,
                "create_by": cls.get("user.id"),
            }
        )
        return {
            "invitationId": row["id"],
            "role": role,
            "createdBy": row["create_by"],
            "createdTime": _iso(row["created_time"]),
            "inviteUrl": self._invite_url(row["id"], code),
            "invitationCode": code,
        }

    async def list_invitation_links(
        self, resource_id: str, resource_type: str = RESOURCE_SPACE
    ) -> list[dict[str, Any]]:
        rows = await repository.list_invitation_link_rows(resource_id, resource_type)
        return [
            {
                "invitationId": row["id"],
                "role": row["role"],
                "createdBy": row["create_by"],
                "createdTime": _iso(row["created_time"]),
                "inviteUrl": self._invite_url(row["id"], row["invitation_code"]),
                "invitationCode": row["invitation_code"],
            }
            for row in rows
        ]

    async def update_invitation_link(
        self,
        resource_id: str,
        invitation_id: str,
        role: str,
        resource_type: str = RESOURCE_SPACE,
    ) -> dict[str, Any]:
        await self.collaborators.validate_user_add_role(
            cls.get("user.id"), role, resource_id, resource_type
        )
        row = await repository.get_invitation_link_row(invitation_id, resource_id, resource_type)
        if row is None:
            raise RuntimeError("Record to update not found")
        updated = await repository.update_invitation_row(invitation_id, {"role": role})
        assert updated is not None
        return {"invitationId": updated["id"], "role": role}

    async def delete_invitation_link(
        self,
        resource_id: str,
        invitation_id: str,
        resource_type: str = RESOURCE_SPACE,
    ) -> None:
        row = await repository.get_invitation_link_row(invitation_id, resource_id, resource_type)
        if row is None:
            raise RuntimeError("Record to update not found")
        await repository.update_invitation_row(
            invitation_id, {"deleted_time": datetime.now(UTC).replace(tzinfo=None)}
        )

    async def accept_invitation_link(self, body: Any) -> dict[str, Any]:
        current_user_id = cls.get("user.id")
        invitation_id = body.invitationId
        if generate_invitation_code(invitation_id) != body.invitationCode:
            raise ApiError(
                "Invalid invitation code",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.invitation.invalidCode"}},
            )
        link = await repository.get_invitation_row(invitation_id)
        if link is None:
            raise ApiError(
                "Invitation link not found",
                HttpErrorCode.NOT_FOUND,
                {"localization": {"i18nKey": "httpErrors.invitation.linkNotFound"}},
            )
        expired_time = link["expired_time"]
        if expired_time is not None and expired_time < datetime.now(UTC).replace(tzinfo=None):
            raise ApiError(
                "Invitation link has expired",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.invitation.linkExpired"}},
            )
        space_id, base_id = link["space_id"], link["base_id"]
        if link["type"] == "email":
            return {"baseId": base_id, "spaceId": space_id}
        resource_id = space_id or base_id
        if not resource_id:
            i18n_key = "httpErrors.space.notFound" if not space_id else "httpErrors.base.notFound"
            raise ApiError(
                "Invalid invitation link: resourceId not found",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": i18n_key}},
            )
        base_space_id: str | None = None
        if base_id:
            from ..base import repository as base_repository

            base = await base_repository.get_base_row(base_id)
            if base is None:
                raise ApiError(
                    "Base not found",
                    HttpErrorCode.NOT_FOUND,
                    {"localization": {"i18nKey": "httpErrors.base.notFound"}},
                )
            base_space_id = base["space_id"]
        resource_ids = [base_space_id, base_id] if base_space_id else [space_id]
        rows = await repository.list_collaborator_rows(resource_ids)
        if not any(
            r["principal_id"] == current_user_id and r["principal_type"] == "user" for r in rows
        ):
            collaborator = {"principalId": current_user_id, "principalType": "user"}
            if space_id:
                await self.collaborators.create_space_collaborator(
                    [collaborator], space_id, link["role"], created_by=link["create_by"]
                )
            else:
                await self.collaborators.create_base_collaborator(
                    [collaborator], base_id, link["role"], created_by=link["create_by"]
                )
            # audit trail
            await repository.insert_invitation_record(
                {
                    "id": cuid(),
                    "invitation_id": link["id"],
                    "inviter": link["create_by"],
                    "accepter": current_user_id,
                    "type": "link",
                    "space_id": space_id,
                    "base_id": base_id,
                }
            )
        return {"baseId": base_id, "spaceId": space_id}
