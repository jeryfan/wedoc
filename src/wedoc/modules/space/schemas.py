"""Request schemas for /api/space — field-level ports of packages/openapi/src/space."""

import re as _re
from typing import Any

from pydantic import Field, StrictBool, field_validator, model_validator

from ...core.errors import ApiError, HttpErrorCode
from ...core.validation import ZodEmailStr, ZodEnumStr, ZodModel

_INTERNAL_SCHEMA_RE = _re.compile(r"^[a-z_]\w*$", _re.IGNORECASE)

SPACE_NAME_MAX_LENGTH = 100

# PrincipalType is a string enum (`user` / `department`); zod validates against
# its values only, so the keys are not accepted.
PRINCIPAL_TYPE_OPTIONS = ["user", "department"]
PRINCIPAL_TYPES = {opt.lower() for opt in PRINCIPAL_TYPE_OPTIONS}

ROLE_OPTIONS = ["owner", "creator", "editor", "commenter", "viewer"]

# ResourceType members accepted by the search `type` param (full string enum,
# lowercase values only).
SEARCHABLE_RESOURCE_OPTIONS = [
    "space", "base", "table", "view", "field", "record",
    "workflow", "app", "dashboard", "folder", "routine",
]

PrincipalTypeStr = ZodEnumStr(PRINCIPAL_TYPE_OPTIONS)
RoleStr = ZodEnumStr(ROLE_OPTIONS)
SearchableResourceStr = ZodEnumStr(SEARCHABLE_RESOURCE_OPTIONS)


class CreateSpaceBody(ZodModel):
    name: str | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if len(v) < 1:
            raise ValueError("Too small: expected string to have >=1 characters")
        if len(v) > SPACE_NAME_MAX_LENGTH:
            raise ValueError("Too big: expected string to have <=100 characters")
        return v


class BaseEntryMapQuery(ZodModel):
    take: str | None = None

    @field_validator("take")
    @classmethod
    def _take(cls, v: str | None) -> int | None:
        if v is None:
            return None
        # z.coerce.number() then int().min(1); query strings arrive as text
        try:
            num = int(v)
        except ValueError:
            raise ValueError("Invalid input: expected number, received NaN") from None
        if num < 1:
            raise ValueError("Too small: expected number to be >=1")
        return num


class SpaceSearchQuery(ZodModel):
    type: SearchableResourceStr | None = None
    search: str
    pageSize: int = 10
    cursor: str | None = None

    @field_validator("search")
    @classmethod
    def _search(cls, v: str) -> str:
        if len(v) < 1:
            raise ValueError("Too small: expected string to have >=1 characters")
        return v

    @field_validator("pageSize", mode="before")
    @classmethod
    def _page_size(cls, v: Any) -> int:
        if v is None or v == "":
            return 10
        try:
            num = int(v)
        except (TypeError, ValueError):
            raise ValueError("Invalid input: expected number, received NaN") from None
        if num < 1:
            raise ValueError("Too small: expected number to be >=1")
        if num > 50:
            raise ValueError("Too big: expected number to be <=50")
        return num


def _coerce_bool(v: Any) -> bool:
    # z.coerce.boolean() on a query string is Boolean(str): empty -> false,
    # anything else (including "false"!) -> true.
    return bool(v)


class ListCollaboratorQuery(ZodModel):
    includeSystem: bool | None = None
    includeBase: bool | None = None
    skip: int | None = None
    take: int | None = None
    search: str | None = None
    type: PrincipalTypeStr | None = None
    orderBy: str | None = None
    principalId: str | None = None

    @field_validator("includeSystem", "includeBase", mode="before")
    @classmethod
    def _coerce_flags(cls, v: Any) -> bool | None:
        if v is None:
            return None
        return _coerce_bool(v)

    @field_validator("skip", "take", mode="before")
    @classmethod
    def _coerce_int(cls, v: Any) -> int | None:
        if v is None or v == "":
            return None
        try:
            return int(v)
        except TypeError, ValueError:
            raise ValueError("Invalid input: expected number, received NaN") from None

    @field_validator("orderBy")
    @classmethod
    def _order_by(cls, v: str | None) -> str | None:
        if v is not None and v not in ("desc", "asc"):
            joined = '"desc"|"asc"'
            raise ValueError(f"Invalid option: expected one of {joined}")
        return v


class AddCollaboratorItem(ZodModel):
    principalId: str
    principalType: PrincipalTypeStr


class AddCollaboratorsBody(ZodModel):
    collaborators: list[AddCollaboratorItem]
    role: RoleStr


class UpdateCollaboratorBody(ZodModel):
    principalId: str
    principalType: PrincipalTypeStr
    role: RoleStr


class DeleteCollaboratorQuery(ZodModel):
    principalId: str
    principalType: PrincipalTypeStr


class InvitationLinkBody(ZodModel):
    role: RoleStr


class EmailInvitationBody(ZodModel):
    emails: list[ZodEmailStr] = Field(min_length=1)
    role: RoleStr


class CreateIntegrationRo(ZodModel):
    # createIntegrationRoSchema: type z.enum(IntegrationType={AI}), enable?, config.
    # A single-member enum reports a literal-style issue ('expected "AI"'), the
    # same for a wrong value and a missing key, so both are handled up front.
    type: str | None = None
    enable: StrictBool | None = None
    config: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def _require_ai_type(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("type") != "AI":
            raise ApiError(
                'Validation error: Invalid input: expected "AI" at "type"',
                HttpErrorCode.VALIDATION_ERROR,
            )
        if isinstance(data, dict) and not isinstance(data.get("config"), dict):
            raise ApiError(
                "Validation error: Invalid input: expected object, received "
                f"{_json_type(data.get('config'))} at \"config\"",
                HttpErrorCode.VALIDATION_ERROR,
            )
        return data


class UpdateIntegrationRo(ZodModel):
    enable: StrictBool | None = None
    config: dict[str, Any] | None = None


_DATA_DB_TARGET_MODES = ["initialize-empty", "migrate-space", "adopt-existing"]


class DataDbPreflightRo(ZodModel):
    url: str
    spaceId: str | None = None
    targetMode: ZodEnumStr(_DATA_DB_TARGET_MODES) = "initialize-empty"
    internalSchema: str | None = None
    confirmLargeMigration: StrictBool | None = None
    switchOnCompletion: StrictBool | None = None

    @field_validator("url")
    @classmethod
    def _url(cls, value: str) -> str:
        if len(value) < 1:
            raise ValueError("Too small: expected string to have >=1 characters")
        return value

    @field_validator("internalSchema")
    @classmethod
    def _internal_schema(cls, value: str | None) -> str | None:
        if value is not None and not _INTERNAL_SCHEMA_RE.match(value):
            raise ValueError("Invalid string: must match pattern /^[a-z_]\\w*$/i")
        return value


def _json_type(value: Any) -> str:
    if value is None:
        return "undefined"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, list):
        return "array"
    return "object"
