"""Request schemas for /api/table/:tableId/record — ports packages/openapi/src/record."""

from typing import Annotated, Any

from pydantic import BeforeValidator, StrictBool

from ...core.validation import ZodEnumStr, ZodExpected, ZodModel

# z.boolean().optional(): a present non-boolean (string/number/null) is rejected
# with "expected boolean, received <type>"; absent stays None.
_StrictBoolOpt = Annotated[StrictBool | None, ZodExpected("boolean")]

_FIELD_KEY_TYPES = ("id", "name", "dbFieldName")
_FIELD_KEY_TYPE_MESSAGE = (
    'Error fieldKeyType, You should set it to "name" or "id" or "dbFieldName"'
)


def _check_field_key_type(v: Any) -> Any:
    if v not in _FIELD_KEY_TYPES:
        raise ValueError(_FIELD_KEY_TYPE_MESSAGE)
    return v


# fieldKeyTypeRoSchema: z.enum(FieldKeyType, {message}) default "name"; the enum
# rejects an explicit null with the same message (the .default only fills undefined).
FieldKeyTypeStr = Annotated[str, BeforeValidator(_check_field_key_type)]


class RecordItem(ZodModel):
    fields: dict[str, Any]


# recordInsertOrderRoSchema: {viewId, anchorId, position} — where a created or
# duplicated record is inserted within a view's manual row order.
RecordPositionStr = ZodEnumStr(["before", "after"])


class RecordInsertOrder(ZodModel):
    viewId: str
    anchorId: str
    position: RecordPositionStr


class RecordCreateBody(ZodModel):
    records: list[RecordItem]
    fieldKeyType: FieldKeyTypeStr = "name"
    typecast: _StrictBoolOpt = None
    order: RecordInsertOrder | None = None


class RecordPatchBody(ZodModel):
    record: RecordItem
    fieldKeyType: FieldKeyTypeStr = "name"
    typecast: _StrictBoolOpt = None


class RecordBulkPatchItem(ZodModel):
    id: str
    fields: dict[str, Any]


class RecordBulkPatchBody(ZodModel):
    records: list[RecordBulkPatchItem]
    fieldKeyType: FieldKeyTypeStr = "name"
    typecast: _StrictBoolOpt = None


class RecordSubmitBody(ZodModel):
    viewId: str
    fields: dict[str, Any]
    typecast: _StrictBoolOpt = None


class InsertAttachmentBody(ZodModel):
    attachments: list[dict[str, Any]]
    anchorId: str | None = None
