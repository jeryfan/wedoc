"""Access-token request schemas — ports packages/openapi/src/access-token."""

from datetime import datetime
from typing import Annotated

from pydantic import StrictBool, field_validator

from ...core.security.permissions import ALL_ACTIONS
from ...core.validation import ZodEnumStr, ZodModel, ZodNullable

_SCOPE_OPTIONS = list(ALL_ACTIONS)
ScopeStr = ZodEnumStr(_SCOPE_OPTIONS)
NullableIds = Annotated[list[str] | None, ZodNullable()]


def _is_valid_date_string(value: str) -> bool:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            datetime.strptime(value, fmt)
            return True
        except ValueError:
            continue
    return False


class _ScopesMixin(ZodModel):
    @field_validator("scopes", check_fields=False)
    @classmethod
    def _scopes_min(cls, value: list[str]) -> list[str]:
        if len(value) < 1:
            raise ValueError("Too small: expected array to have >=1 items")
        return value


class CreateAccessTokenRo(_ScopesMixin):
    name: str
    description: str | None = None
    scopes: list[ScopeStr]
    spaceIds: NullableIds = None
    baseIds: NullableIds = None
    hasFullAccess: StrictBool | None = None
    expiredTime: str

    @field_validator("name")
    @classmethod
    def _name_min(cls, value: str) -> str:
        if len(value) < 1:
            raise ValueError("Too small: expected string to have >=1 characters")
        return value

    @field_validator("spaceIds", "baseIds")
    @classmethod
    def _ids_min(cls, value: list[str] | None) -> list[str] | None:
        if isinstance(value, list) and len(value) < 1:
            raise ValueError("Too small: expected array to have >=1 items")
        return value

    @field_validator("expiredTime")
    @classmethod
    def _expired(cls, value: str) -> str:
        if not _is_valid_date_string(value):
            raise ValueError("expiredTime: Invalid Date ")
        return value


class UpdateAccessTokenRo(_ScopesMixin):
    name: str
    description: str | None = None
    scopes: list[ScopeStr]
    spaceIds: NullableIds = None
    baseIds: NullableIds = None
    hasFullAccess: StrictBool | None = None


class RefreshAccessTokenRo(ZodModel):
    expiredTime: str
