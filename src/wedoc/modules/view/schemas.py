"""Request schemas for /api/table/:tableId/view — ports packages/openapi/src/view."""

from typing import Any

from ...core.validation import ZodEnumStr, ZodModel, ZodNullableStr

ViewTypeStr = ZodEnumStr(["grid", "calendar", "kanban", "form", "gallery", "plugin"])


class ViewCreateBody(ZodModel):
    name: str | None = None
    type: ViewTypeStr
    description: ZodNullableStr = None
    order: float | None = None
    options: dict[str, Any] | None = None
    sort: dict[str, Any] | None = None
    filter: dict[str, Any] | None = None
    group: list[dict[str, Any]] | None = None
    isLocked: bool | None = None
    columnMeta: dict[str, Any] | None = None


class ViewNameBody(ZodModel):
    name: str


class ViewDescriptionBody(ZodModel):
    description: ZodNullableStr = None


class ViewLockedBody(ZodModel):
    isLocked: bool | None = None


class ShareMetaBody(ZodModel):
    # share-meta body carries meta fields directly (no shareMeta wrapper;
    # a wrapper key is stripped like ref does).
    allowCopy: bool | None = None
    includeHiddenField: bool | None = None
    password: str | None = None
    includeRecords: bool | None = None
    submit: dict[str, Any] | None = None
    allowEdit: bool | None = None


class ViewFilterBody(ZodModel):
    filter: dict[str, Any] | None = None


class ViewSortBody(ZodModel):
    sort: dict[str, Any] | None = None


class ViewGroupBody(ZodModel):
    group: list[dict[str, Any]] | None = None


class ViewOptionsBody(ZodModel):
    options: dict[str, Any] | None = None


class ColumnMetaItem(ZodModel):
    fieldId: str
    columnMeta: dict[str, Any]


class ViewOrderBody(ZodModel):
    anchorId: str
    position: str


class RecordOrderBody(ZodModel):
    anchorId: str
    position: str
    recordIds: list[str]


class ManualSortBody(ZodModel):
    sortObjs: list[dict[str, Any]]
