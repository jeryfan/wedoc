"""Request schemas for /api/base/:baseId/node — ports packages/openapi/src/base-node."""

from typing import Any

from pydantic import field_validator

from ...core.validation import ZodEnumStr, ZodModel, ZodNullableStr
from ..table.schemas import FieldKeyTypeStr, FieldRoBody, RecordRoBody, ViewRoBody

RESOURCE_FOLDER = "folder"
TABLE = "table"
DASHBOARD = "dashboard"
WORKFLOW = "workflow"
APP = "app"
RESOURCE_TYPE_OPTIONS = [TABLE, DASHBOARD, WORKFLOW, APP, RESOURCE_FOLDER]
ResourceTypeStr = ZodEnumStr(RESOURCE_TYPE_OPTIONS)
PositionStr = ZodEnumStr(["before", "after"])

_UNSET: Any = object()


def _required_name(v: Any) -> Any:
    # z.string().trim().min(1)
    if not isinstance(v, str):
        return v
    trimmed = v.strip()
    if len(trimmed) < 1:
        raise ValueError("Too small: expected string to have >=1 characters")
    return trimmed


class CreateNodeBody(ZodModel):
    resourceType: ResourceTypeStr
    parentId: ZodNullableStr = None
    name: str

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, v: Any) -> Any:
        return _required_name(v)


class CreateTableNodeBody(ZodModel):
    """resourceType=table branch: base-node fields spread with the table RO
    (tableRoWithDefaultSchema). name is optional here (the table default applies)."""

    resourceType: ResourceTypeStr
    parentId: ZodNullableStr = None
    name: str | None = None
    description: ZodNullableStr = None
    icon: ZodNullableStr = None
    dbTableName: str | None = None
    fieldKeyType: FieldKeyTypeStr = "name"
    fields: list[FieldRoBody] | None = None
    views: list[ViewRoBody] | None = None
    records: list[RecordRoBody] | None = None

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, v: Any) -> Any:
        if v is None:
            return v
        return _required_name(v)


class CreateDashboardNodeBody(ZodModel):
    """resourceType=dashboard branch: base-node fields spread with createDashboardRoSchema
    (name is z.string(), no trim/min)."""

    resourceType: ResourceTypeStr
    parentId: ZodNullableStr = None
    name: str


class UpdateNodeBody(ZodModel):
    name: str | None = None
    icon: ZodNullableStr = None

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, v: Any) -> Any:
        if v is None:
            return v
        return _required_name(v)

    @field_validator("icon", mode="before")
    @classmethod
    def _icon(cls, v: Any) -> Any:
        # z.string().trim().optional().nullable(): trim strings, keep null/absent.
        return v.strip() if isinstance(v, str) else v


class MoveNodeBody(ZodModel):
    parentId: ZodNullableStr = None
    anchorId: str | None = None
    position: PositionStr | None = None

    def parent_set(self) -> bool:
        return "parentId" in self.model_fields_set


class DuplicateNodeBody(ZodModel):
    name: str | None = None

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, v: Any) -> Any:
        if v is None:
            return v
        return _required_name(v)


class CreateFolderBody(ZodModel):
    name: str

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, v: Any) -> Any:
        return _required_name(v)


class UpdateFolderBody(ZodModel):
    name: str

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, v: Any) -> Any:
        return _required_name(v)
