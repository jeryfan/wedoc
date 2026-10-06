"""Request schemas for /api/base — field-level ports of packages/openapi/src/base."""

from typing import Annotated, Any

from pydantic import Field, StrictBool, field_validator

from ...core.validation import ZodEmailStr, ZodEnumStr, ZodExpected, ZodModel, ZodNullableStr
from ..space.schemas import (
    PRINCIPAL_TYPE_OPTIONS,
    ROLE_OPTIONS,
    AddCollaboratorItem,
    PrincipalTypeStr,
    _coerce_bool,
)

# z.boolean().optional(): reject a present non-boolean; absent stays None.
_StrictBoolOpt = Annotated[StrictBool | None, ZodExpected("boolean")]

BASE_ROLE_OPTIONS = [r for r in ROLE_OPTIONS if r != "owner"]
BaseRoleStr = ZodEnumStr(BASE_ROLE_OPTIONS)
# collaborator-get-list roleSchema accepts every role (owner included).
RoleStr = ZodEnumStr(ROLE_OPTIONS)
PositionStr = ZodEnumStr(["before", "after"])


class CreateBaseBody(ZodModel):
    spaceId: str
    name: str | None = None
    icon: str | None = None


class UpdateBaseBody(ZodModel):
    name: str | None = None
    icon: ZodNullableStr = None


class UpdateOrderBody(ZodModel):
    anchorId: str
    position: PositionStr


class MoveBaseBody(ZodModel):
    spaceId: str


class DuplicateBaseBody(ZodModel):
    fromBaseId: str
    spaceId: str
    withRecords: _StrictBoolOpt = None
    name: str | None = None
    baseId: str | None = None
    nodes: list[str] | None = None
    shareId: str | None = None
    timeZone: str | None = None


class CreateFromTemplateBody(ZodModel):
    spaceId: str
    templateId: str
    withRecords: _StrictBoolOpt = None
    baseId: str | None = None
    timeZone: str | None = None


class NotifyVo(ZodModel):
    token: str
    size: float
    url: str
    path: str
    mimetype: str
    width: float | None = None
    height: float | None = None
    presignedUrl: str


class ImportBaseBody(ZodModel):
    notify: NotifyVo
    spaceId: str


class AddBaseCollaboratorsBody(ZodModel):
    collaborators: list[AddCollaboratorItem]
    role: BaseRoleStr


class UpdateBaseCollaboratorBody(ZodModel):
    principalId: str
    principalType: PrincipalTypeStr
    role: BaseRoleStr


class ListBaseCollaboratorQuery(ZodModel):
    includeSystem: bool | None = None
    skip: int | None = None
    take: int | None = None
    search: str | None = None
    type: PrincipalTypeStr | None = None
    role: list[RoleStr] | None = None

    @field_validator("includeSystem", mode="before")
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
        except (TypeError, ValueError):
            raise ValueError("Invalid input: expected number, received NaN") from None

    @field_validator("role", mode="before")
    @classmethod
    def _role_array(cls, v: Any) -> Any:
        # zod array(): a single query value stays a string and is rejected.
        if isinstance(v, str):
            raise ValueError("Invalid input: expected array, received string")
        return v

    @field_validator("type")
    @classmethod
    def _type(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if v.lower() not in {opt.lower() for opt in PRINCIPAL_TYPE_OPTIONS}:
            joined = "|".join(f'"{opt}"' for opt in PRINCIPAL_TYPE_OPTIONS)
            raise ValueError(f"Invalid option: expected one of {joined}")
        return v.lower()


class ListBaseCollaboratorUserQuery(ZodModel):
    search: str | None = None
    skip: int | None = None
    take: int | None = None
    includeSystem: bool | None = None
    orderBy: str | None = None

    @field_validator("includeSystem", mode="before")
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
        except (TypeError, ValueError):
            raise ValueError("Invalid input: expected number, received NaN") from None

    @field_validator("orderBy")
    @classmethod
    def _order_by(cls, v: str | None) -> str | None:
        if v is not None and v not in ("desc", "asc"):
            joined = "|".join(f'"{opt}"' for opt in ("desc", "asc"))
            raise ValueError(f"Invalid option: expected one of {joined}")
        return v


class BaseInvitationLinkBody(ZodModel):
    role: BaseRoleStr


class BaseEmailInvitationBody(ZodModel):
    emails: list[ZodEmailStr] = Field(min_length=1)
    role: BaseRoleStr


class PublishBaseBody(ZodModel):
    title: str
    description: str
    cover: dict[str, Any] | None = None
    nodes: list[str] | None = None
    includeData: bool | None = None
    defaultActiveNodeId: str | None = None
