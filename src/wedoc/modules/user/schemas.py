"""Request schemas for /api/user — field-level ports of packages/openapi/src/user."""

from typing import Annotated, Any

from pydantic import StrictBool, field_validator

from ...core.validation import ZodEnumStr, ZodExpected, ZodModel

USER_NAME_MAX_LENGTH = 100

# The EE LastVisitResourceType enum's zod schema accepts both the enum values
# (lowercase) and keys (Capitalized) — 8 names (adds routine over OSS's 7),
# interleaved lower/Cap per name in declaration order. Only the lowercase forms
# are service-valid on GET (base + every Capitalized form -> "Invalid resource
# type"); POST accepts all 16.
LAST_VISIT_RESOURCE_TYPE_OPTIONS: list[str] = [
    "space",
    "Space",
    "base",
    "Base",
    "table",
    "Table",
    "view",
    "View",
    "dashboard",
    "Dashboard",
    "workflow",
    "Workflow",
    "app",
    "App",
    "routine",
    "Routine",
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
    email: Annotated[StrictBool | None, ZodExpected("boolean")] = None
    appBuilderChatIntroDismissed: Annotated[StrictBool | None, ZodExpected("boolean")] = None


class UpdateUserLangBody(ZodModel):
    lang: str


class TrackEventBody(ZodModel):
    event: str
    properties: Annotated[dict[str, Any] | None, ZodExpected("record")] = None

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
