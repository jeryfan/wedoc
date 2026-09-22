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
from ...core.ids import IdPrefix, new_id
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
