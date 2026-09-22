"""Request schemas for /api/base/:baseId/node — ports packages/openapi/src/base-node."""

from typing import Any

from pydantic import field_validator

from ...core.validation import ZodEnumStr, ZodModel, ZodNullableStr

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


class UpdateNodeBody(ZodModel):
    name: str | None = None
    icon: ZodNullableStr = None

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, v: Any) -> Any:
        if v is None:
            return v
        return _required_name(v)


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
