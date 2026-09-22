"""Request schemas for /api/invitation — ports packages/openapi/src/invitation."""

from typing import Any

from pydantic import field_validator

from ...core.ids import IdPrefix
from ...core.validation import ZodModel


class AcceptInvitationLinkBody(ZodModel):
    invitationCode: str
    invitationId: str

    @field_validator("invitationId", mode="before")
    @classmethod
    def _prefix(cls, v: Any) -> Any:
        # z.string().startsWith(IdPrefix.Invitation)
        if not isinstance(v, str):
            return v
        if not v.startswith(str(IdPrefix.INVITATION)):
            raise ValueError(f'Invalid string: must start with "{IdPrefix.INVITATION}"')
        return v
