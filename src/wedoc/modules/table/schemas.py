"""Request schemas for /api/base/:baseId/table — ports packages/openapi/src/table."""

from typing import Any

from pydantic import field_validator

from ...core.validation import ZodEnumStr, ZodModel, ZodNullableStr

FieldTypeStr = ZodEnumStr(
    [
        "singleLineText",
        "longText",
        "number",
        "singleSelect",
        "multipleSelect",
        "attachment",
        "checkbox",
        "rating",
        "date",
        "autoNumber",
        "createdTime",
        "lastModifiedTime",
        "createdBy",
        "lastModifiedBy",
        "user",
        "button",
    ]
)
ViewTypeStr = ZodEnumStr(["grid", "kanban", "form", "calendar", "gallery"])
FieldKeyTypeStr = ZodEnumStr(["name", "id"])
PositionStr = ZodEnumStr(["before", "after"])

PRIMARY_SUPPORTED_TYPES = {
    "singleLineText",
    "longText",
    "user",
    "multipleSelect",
    "singleSelect",
    "date",
    "number",
    "rating",
    "formula",
    "createdTime",
    "lastModifiedTime",
    "createdBy",
    "lastModifiedBy",
    "autoNumber",
}


class FieldRoBody(ZodModel):
    name: str
    type: FieldTypeStr
    id: str | None = None
    description: ZodNullableStr = None
    isPrimary: bool | None = None
    notNull: bool | None = None
    unique: bool | None = None
    dbFieldName: str | None = None
    options: dict[str, Any] | None = None

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, v: Any) -> Any:
        # z.string().trim().min(1)
        if not isinstance(v, str):
            return v
        trimmed = v.strip()
        if len(trimmed) < 1:
            raise ValueError("Too small: expected string to have >=1 characters")
        return trimmed


class ViewRoBody(ZodModel):
    name: str
    type: ViewTypeStr
    description: ZodNullableStr = None
    filter: Any = None
    sort: Any = None
    group: Any = None
    options: dict[str, Any] | None = None
    columnMeta: dict[str, Any] | None = None


class RecordRoBody(ZodModel):
    fields: dict[str, Any]


class CreateTableBody(ZodModel):
    name: str | None = None
    description: ZodNullableStr = None
    icon: ZodNullableStr = None
    dbTableName: str | None = None
    fieldKeyType: FieldKeyTypeStr = "name"
    fields: list[FieldRoBody] | None = None
    views: list[ViewRoBody] | None = None
    records: list[RecordRoBody] | None = None

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, v: Any) -> Any:
        if v is None:
            return v
        if not isinstance(v, str):
            return v
        trimmed = v.strip()
        if len(trimmed) < 1:
            raise ValueError("Too small: expected string to have >=1 characters")
        return trimmed


class TableNameBody(ZodModel):
    name: str

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, v: Any) -> Any:
        if not isinstance(v, str):
            return v
        trimmed = v.strip()
        if len(trimmed) < 1:
            raise ValueError("Too small: expected string to have >=1 characters")
        return trimmed


class TableIconBody(ZodModel):
    icon: ZodNullableStr = None


class TableDescriptionBody(ZodModel):
    description: ZodNullableStr = None


class DbTableNameBody(ZodModel):
    dbTableName: str


class UpdateOrderBody(ZodModel):
    anchorId: str
    position: PositionStr
