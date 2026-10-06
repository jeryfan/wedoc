"""Plugin request schemas — ports packages/openapi/src/plugin."""

from typing import Annotated
from urllib.parse import urlparse

from pydantic import StrictBool, field_validator

from ...core.validation import ZodEnumStr, ZodExpected, ZodModel

PLUGIN_POSITIONS = ["dashboard", "view", "contextMenu", "panel"]
PositionStr = ZodEnumStr(PLUGIN_POSITIONS)

# pluginBaseActions (base|* + table/view/field/record/table_record_history/automation/app)
PLUGIN_BASE_ACTIONS = [
    "base|read",
    "base|update",
    "base|invite_email",
    "base|invite_link",
    "base|table_import",
    "base|table_export",
    "base|authority_matrix_config",
    "base|db_connection",
    "base|query_data",
    "table|create",
    "table|delete",
    "table|read",
    "table|update",
    "table|import",
    "table|export",
    "table|trash_read",
    "table|trash_update",
    "table|trash_reset",
    "table|archive_read",
    "table|archive_manage",
    "view|create",
    "view|delete",
    "view|read",
    "view|update",
    "view|share",
    "field|create",
    "field|delete",
    "field|read",
    "field|update",
    "record|create",
    "record|delete",
    "record|read",
    "record|update",
    "record|comment",
    "record|copy",
    "record|archive",
    "table_record_history|read",
    "automation|create",
    "automation|delete",
    "automation|read",
    "automation|update",
    "app|create",
    "app|delete",
    "app|read",
    "app|update",
]
PluginScopeStr = ZodEnumStr(PLUGIN_BASE_ACTIONS)


def _url(value: str) -> str:
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("Invalid URL")
    return value


class CreatePluginRo(ZodModel):
    name: str
    description: str | None = None
    detailDesc: str | None = None
    logo: str
    url: str | None = None
    config: dict | None = None
    helpUrl: str | None = None
    positions: list[PositionStr]
    i18n: dict | None = None
    autoCreateMember: Annotated[StrictBool | None, ZodExpected("boolean")] = None

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if len(v) < 1:
            raise ValueError("Too small: expected string to have >=1 characters")
        if len(v) > 20:
            raise ValueError("Too big: expected string to have <=20 characters")
        return v

    @field_validator("positions")
    @classmethod
    def _positions(cls, v: list[str]) -> list[str]:
        if len(v) < 1:
            raise ValueError("Too small: expected array to have >=1 items")
        return v

    @field_validator("url", "helpUrl")
    @classmethod
    def _urls(cls, v: str | None) -> str | None:
        if v is not None:
            _url(v)
        return v


class UpdatePluginRo(CreatePluginRo):
    logo: str | None = None

    @field_validator("name")
    @classmethod
    def _name_update(cls, v: str) -> str:
        return v


class PluginGetTokenRo(ZodModel):
    baseId: str
    secret: str
    scopes: list[PluginScopeStr]
    authCode: str

    @field_validator("scopes")
    @classmethod
    def _scopes(cls, v: list[str]) -> list[str]:
        if len(v) < 1:
            raise ValueError("Too small: expected array to have >=1 items")
        return v


class PluginRefreshTokenRo(ZodModel):
    refreshToken: str
    secret: str
