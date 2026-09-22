"""Request schemas for /api/table/:tableId/record — ports packages/openapi/src/record."""

from typing import Any

from ...core.validation import ZodEnumStr, ZodModel

FieldKeyTypeStr = ZodEnumStr(["name", "id"])


class RecordItem(ZodModel):
    fields: dict[str, Any]


class RecordCreateBody(ZodModel):
    records: list[RecordItem]
    fieldKeyType: FieldKeyTypeStr = "name"
    typecast: bool | None = None


class RecordPatchBody(ZodModel):
    record: RecordItem
    fieldKeyType: FieldKeyTypeStr = "name"
    typecast: bool | None = None


class RecordBulkPatchItem(ZodModel):
    id: str
    fields: dict[str, Any]


class RecordBulkPatchBody(ZodModel):
    records: list[RecordBulkPatchItem]
    fieldKeyType: FieldKeyTypeStr = "name"
    typecast: bool | None = None


class RecordSubmitBody(ZodModel):
    viewId: str
    fields: dict[str, Any]
    typecast: bool | None = None
