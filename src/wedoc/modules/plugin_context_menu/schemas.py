"""Plugin-context-menu request schemas — ports packages/openapi/src/plugin-context-menu."""

from typing import Any

from ...core.validation import ZodEnumStr, ZodModel

PositionStr = ZodEnumStr(["before", "after"])


class InstallRo(ZodModel):
    name: str | None = None
    pluginId: str


class RenameRo(ZodModel):
    name: str


class UpdateStorageRo(ZodModel):
    storage: dict[str, Any] | None = None


class MoveRo(ZodModel):
    anchorId: str
    position: PositionStr
