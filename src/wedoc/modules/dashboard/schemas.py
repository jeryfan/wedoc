"""Dashboard request schemas — ports packages/openapi/src/dashboard."""

from typing import Any

from pydantic import field_validator

from ...core.validation import ZodModel


class CreateDashboardRo(ZodModel):
    name: str


class RenameDashboardRo(ZodModel):
    name: str


class LayoutItem(ZodModel):
    pluginInstallId: str
    x: float
    y: float
    w: float
    h: float


class UpdateLayoutDashboardRo(ZodModel):
    layout: list[LayoutItem]


class InstallPluginRo(ZodModel):
    name: str
    pluginId: str


class UpdateStorageRo(ZodModel):
    storage: dict[str, Any] | None = None


class DuplicateDashboardRo(ZodModel):
    name: str | None = None


class DuplicateInstalledPluginRo(ZodModel):
    name: str | None = None

    @field_validator("name")
    @classmethod
    def _noop(cls, v: str | None) -> str | None:
        return v
