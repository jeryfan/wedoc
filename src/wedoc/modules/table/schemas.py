"""Request schemas for /api/base/:baseId/table — ports packages/openapi/src/table."""

import re
from typing import Annotated, Any

from pydantic import BeforeValidator, StrictBool, field_validator

from ...core.validation import ZodEnumStr, ZodExpected, ZodModel, ZodMultiError, ZodNullableStr

# create() allows a letter start only; the db-table-name endpoint also allows "_".
# re.ASCII keeps \w to [A-Za-z0-9_] so it tracks the JS /i regex (no Unicode words).
_CREATE_DB_TABLE_NAME_RE = re.compile(r"^[a-z]\w{0,62}$", re.IGNORECASE | re.ASCII)
_DB_TABLE_NAME_RE = re.compile(r"^[a-z_]\w{0,62}$", re.IGNORECASE | re.ASCII)
_FIELD_KEY_TYPES = ("id", "name", "dbFieldName")
_FIELD_KEY_TYPE_MESSAGE = (
    'Error fieldKeyType, You should set it to "name" or "id" or "dbFieldName"'
)
# zod 4.1.8 z.string().emoji(): ^(\p{Extended_Pictographic}|\p{Emoji_Component})+$/u,
# enumerated as code-point ranges because re has no \p{...}.
_EMOJI_RE = re.compile(
    "^(?:["
    "#*0-9"
    "\u00a9\u00ae\u203c\u2049\u2122\u2139"
    "\u2194-\u2199\u21a9-\u21aa"
    "\u231a-\u231b\u2328\u23cf\u23e9-\u23f3\u23f8-\u23fa\u24c2"
    "\u25aa-\u25ab\u25b6\u25c0\u25fb-\u25fe"
    "\u2600-\u27bf\u2934-\u2935"
    "\u2b05-\u2b07\u2b1b-\u2b1c\u2b50\u2b55"
    "\u3030\u303d\u3297\u3299"
    "\u200d\u20e3\ufe0f"
    "\U0001f000-\U0001faff"
    "\U000e0020-\U000e007f"
    "])+$"
)

FieldTypeStr = ZodEnumStr(
    [
        "singleLineText",
        "longText",
        "user",
        "attachment",
        "checkbox",
        "multipleSelect",
        "singleSelect",
        "date",
        "number",
        "rating",
        "formula",
        "rollup",
        "conditionalRollup",
        "link",
        "createdTime",
        "lastModifiedTime",
        "createdBy",
        "lastModifiedBy",
        "autoNumber",
        "button",
    ]
)
ViewTypeStr = ZodEnumStr(["grid", "kanban", "form", "calendar", "gallery"])
PositionStr = ZodEnumStr(["before", "after"])


def _check_field_key_type(v: Any) -> Any:
    if v not in _FIELD_KEY_TYPES:
        raise ValueError(_FIELD_KEY_TYPE_MESSAGE)
    return v


# fieldKeyTypeRoSchema: z.enum(FieldKeyType, {message}) default "name"; the enum
# rejects an explicit null with the same message (the .default only fills undefined).
FieldKeyTypeStr = Annotated[str, BeforeValidator(_check_field_key_type)]

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
    fieldKeyType: str = "name"
    fields: list[FieldRoBody] | None = None
    views: list[ViewRoBody] | None = None
    records: list[RecordRoBody] | None = None

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, v: Any) -> Any:
        # tableFullVoSchema.shape.name.min(1).optional(): min(1), no trim.
        if v is None or not isinstance(v, str):
            return v
        if len(v) < 1:
            raise ValueError("Too small: expected string to have >=1 characters")
        return v

    @field_validator("dbTableName", mode="before")
    @classmethod
    def _db_table_name(cls, v: Any) -> Any:
        if v is None or not isinstance(v, str):
            return v
        if not _CREATE_DB_TABLE_NAME_RE.match(v):
            raise ValueError("Invalid name format")
        return v

    @field_validator("icon", mode="before")
    @classmethod
    def _icon(cls, v: Any) -> Any:
        if v is None or not isinstance(v, str):
            return v
        if not _EMOJI_RE.match(v):
            raise ValueError("Invalid emoji")
        return v

    @field_validator("fieldKeyType", mode="before")
    @classmethod
    def _field_key_type(cls, v: Any) -> Any:
        return _check_field_key_type(v)


class TableNameBody(ZodModel):
    # tableNameRoSchema = z.object({ name: z.string() }) — no trim, no min.
    name: str


class TableIconBody(ZodModel):
    # tableIconRoSchema = z.object({ icon: z.string().emoji().nullable() }) — required key.
    icon: ZodNullableStr

    @field_validator("icon", mode="before")
    @classmethod
    def _icon(cls, v: Any) -> Any:
        if v is None or not isinstance(v, str):
            return v
        if not _EMOJI_RE.match(v):
            raise ValueError("Invalid emoji")
        return v


class TableDescriptionBody(ZodModel):
    # tableDescriptionRoSchema = z.object({ description: z.string().nullable() }) — required key.
    description: ZodNullableStr


class DbTableNameBody(ZodModel):
    dbTableName: str

    @field_validator("dbTableName", mode="before")
    @classmethod
    def _db_table_name(cls, v: Any) -> Any:
        if not isinstance(v, str):
            return v
        messages: list[str] = []
        if len(v) < 1:
            messages.append("Table name cannot be empty")
        if not _DB_TABLE_NAME_RE.match(v):
            messages.append("Invalid name format")
        if messages:
            if len(messages) == 1:
                raise ValueError(messages[0])
            raise ZodMultiError(messages)
        return v


class UpdateOrderBody(ZodModel):
    anchorId: str
    position: PositionStr


class DuplicateTableBody(ZodModel):
    name: str
    includeRecords: Annotated[StrictBool, ZodExpected("boolean")]


class ToggleIndexBody(ZodModel):
    type: str

    @field_validator("type", mode="before")
    @classmethod
    def _type(cls, v: Any) -> Any:
        # z.enum(TableIndex) with the single member "search" reports this literal
        # message on any other value.
        if v != "search":
            raise ValueError('Invalid input: expected "search"')
        return v
