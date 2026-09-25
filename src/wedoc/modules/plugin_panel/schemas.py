"""Plugin-panel request schemas — ports packages/openapi/src/plugin-panel."""

from typing import Any

from ...core.validation import ZodModel


class CreatePanelRo(ZodModel):
    name: str


class RenamePanelRo(ZodModel):
    name: str


class LayoutItem(ZodModel):
    pluginInstallId: str
    x: float
    y: float
    w: float
    h: float


class UpdateLayoutRo(ZodModel):
    layout: list[LayoutItem]


class InstallRo(ZodModel):
    name: str | None = None
    pluginId: str


class UpdateStorageRo(ZodModel):
    storage: dict[str, Any] | None = None


class DuplicatePanelRo(ZodModel):
    name: str | None = None


class DuplicateInstalledRo(ZodModel):
    name: str | None = None
