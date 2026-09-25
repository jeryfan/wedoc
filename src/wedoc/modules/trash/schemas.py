"""Trash request schemas — ports packages/openapi/src/trash."""

from ...core.validation import ZodEnumStr, ZodModel


class TrashRo(ZodModel):
    spaceId: str | None = None
    resourceType: ZodEnumStr(["space", "base"])


class TrashItemsRo(ZodModel):
    resourceId: str
    resourceType: ZodEnumStr(["base", "table"])
    cursor: str | None = None
    pageSize: int | None = None
