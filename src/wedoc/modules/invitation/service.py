"""Invitation domain service — ports features/invitation/invitation.service.ts.

Link and email invitations create collaborator + invitation + invitation_record
rows; email delivery is a decoupled side effect (mailer, SMTP-gated) as upstream.
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

    async def email_invitation(
        self,
        resource_id: str,
        emails: list[str],
        role: str,
        resource_type: str = RESOURCE_SPACE,
    ) -> dict[str, Any]:
        from datetime import timedelta

        from ..base import repository as base_repository
        from ..user.service import UserService

        space_id = resource_id if resource_type == RESOURCE_SPACE else None
        base_id = resource_id if resource_type == RESOURCE_BASE else None
        if resource_type == RESOURCE_SPACE:
            if await repository.get_space_row(resource_id) is None:
                raise ApiError(
                    "Space not found",
                    HttpErrorCode.NOT_FOUND,
                    {"localization": {"i18nKey": "httpErrors.space.notFound"}},
                )
        elif await base_repository.get_base_row(resource_id) is None:
            raise ApiError(
                "Base not found",
                HttpErrorCode.NOT_FOUND,
                {"localization": {"i18nKey": "httpErrors.base.notFound"}},
            )
        await self.collaborators.validate_user_add_role(
            cls.get("user.id"), role, resource_id, resource_type
        )
        user_service = UserService()
        expired = datetime.now(UTC).replace(tzinfo=None) + timedelta(days=30)
        result: dict[str, Any] = {}
        for email in emails:
            user = await user_service.get_user_by_email(email.lower())
            if user is None:
                user = await user_service.create_user({"email": email.lower()})
            collaborator = {"principalId": user["id"], "principalType": "user"}
            if resource_type == RESOURCE_SPACE:
                await self.collaborators.create_space_collaborator(
                    [collaborator], resource_id, role
                )
            else:
                await self.collaborators.create_base_collaborator(
                    [collaborator], resource_id, role
                )
            invitation_id = new_id(IdPrefix.INVITATION)
            await repository.insert_invitation(
                {
                    "id": invitation_id,
                    "invitation_code": generate_invitation_code(invitation_id),
                    "space_id": space_id,
                    "base_id": base_id,
                    "role": role,
                    "type": "email",
                    "expired_time": expired,
                    "create_by": cls.get("user.id"),
                }
            )
            await repository.insert_invitation_record(
                {
                    "id": cuid(),
                    "invitation_id": invitation_id,
                    "inviter": cls.get("user.id"),
                    "accepter": user["id"],
                    "type": "email",
                    "space_id": space_id,
                    "base_id": base_id,
                }
            )
            result[email] = {"invitationId": invitation_id}
        return result

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
