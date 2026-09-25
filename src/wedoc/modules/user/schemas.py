"""Request schemas for /api/user — field-level ports of packages/openapi/src/user."""

from typing import Any

from pydantic import StrictBool, field_validator

from ...core.validation import ZodEnumStr, ZodModel

USER_NAME_MAX_LENGTH = 100

# LastVisitResourceType is a TS string enum, and z.enum(<string enum>) accepts
# only its values (string enums have no reverse-mapping keys) — the exact set is
# these 7 lowercase names, ordered as declared.
LAST_VISIT_RESOURCE_TYPE_OPTIONS: list[str] = [
    "space",
    "base",
    "table",
    "view",
    "dashboard",
    "workflow",
    "app",
]

LAST_VISIT_RESOURCE_TYPES = set(LAST_VISIT_RESOURCE_TYPE_OPTIONS)


class UpdateUserNameBody(ZodModel):
    name: str

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if len(v) < 1:
            raise ValueError("Too small: expected string to have >=1 characters")
        if len(v) > USER_NAME_MAX_LENGTH:
            raise ValueError("Too big: expected string to have <=100 characters")
        return v


class UserNotifyMetaBody(ZodModel):
    email: StrictBool | None = None
    appBuilderChatIntroDismissed: StrictBool | None = None


class UpdateUserLangBody(ZodModel):
    lang: str


class TrackEventBody(ZodModel):
    event: str
    properties: dict[str, Any] | None = None

    @field_validator("event")
    @classmethod
    def _event(cls, v: str) -> str:
        if len(v) < 1:
            raise ValueError("Too small: expected string to have >=1 characters")
        if len(v) > 100:
            raise ValueError("Too big: expected string to have <=100 characters")
        return v


ResourceTypeStr = ZodEnumStr(LAST_VISIT_RESOURCE_TYPE_OPTIONS)


class GetUserLastVisitQuery(ZodModel):
    resourceType: ResourceTypeStr
    parentResourceId: str


class UpdateUserLastVisitBody(ZodModel):
    resourceType: ResourceTypeStr
    resourceId: str
    parentResourceId: str
    childResourceId: str | None = None


class GetUserLastVisitBaseNodeQuery(ZodModel):
    parentResourceId: str
