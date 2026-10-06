"""OAuth client request schemas — ports packages/openapi/src/oauth."""

from typing import Annotated
from urllib.parse import urlparse

from pydantic import AfterValidator, StrictBool, field_validator

from ...core.validation import ZodExpected, ZodModel


def zod_url(value: str) -> str:
    """z.string().url(): valid when the URL constructor would accept it."""
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("Invalid URL")
    return value


# z.array(z.string().url()): each element is validated, so a bad element
# reports its index (e.g. redirectUris[0]).
RedirectUri = Annotated[str, AfterValidator(zod_url)]

OAUTH_ACTIONS = [
    "app|create",
    "app|delete",
    "app|read",
    "app|update",
    "base|create",
    "base|delete",
    "base|read",
    "base|read_all",
    "base|update",
    "base|table_import",
    "base|table_export",
    "base|query_data",
    "base|authority_matrix_config",
    "table|create",
    "table|delete",
    "table|export",
    "table|import",
    "table|read",
    "table|update",
    "table|trash_read",
    "table|trash_update",
    "table|trash_reset",
    "table|archive_read",
    "table|archive_manage",
    "view|create",
    "view|delete",
    "view|read",
    "view|update",
    "field|create",
    "field|delete",
    "field|read",
    "field|update",
    "record|comment",
    "record|create",
    "record|delete",
    "record|read",
    "record|update",
    "record|archive",
    "automation|create",
    "automation|delete",
    "automation|read",
    "automation|update",
    "routine|create",
    "routine|delete",
    "routine|read",
    "routine|update",
    "user|email_read",
    "user|integrations",
]


def _check_scope(value: str) -> str:
    # z.array(z.enum(...)): each element is validated, so a bad element reports
    # its index (scopes[0]) rather than the field.
    if value not in OAUTH_ACTIONS:
        joined = "|".join(f'"{v}"' for v in OAUTH_ACTIONS)
        raise ValueError(f"Invalid option: expected one of {joined}")
    return value


Scope = Annotated[str, AfterValidator(_check_scope)]


class OAuthCreateRo(ZodModel):
    name: str
    description: str | None = None
    homepage: str
    logo: str | None = None
    scopes: list[Scope] | None = None
    redirectUris: list[RedirectUri]
    allowDeviceFlow: Annotated[StrictBool | None, ZodExpected("boolean")] = None

    @field_validator("homepage")
    @classmethod
    def _homepage(cls, value: str) -> str:
        return zod_url(value)

    @field_validator("scopes")
    @classmethod
    def _scopes(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        seen: dict[str, None] = {}
        for item in value:
            seen.setdefault(item, None)
        return list(seen.keys())

    @field_validator("redirectUris")
    @classmethod
    def _redirect_uris(cls, value: list[str]) -> list[str]:
        if len(value) < 1:
            raise ValueError("Too small: expected array to have >=1 items")
        return value


class OAuthUpdateRo(OAuthCreateRo):
    pass
