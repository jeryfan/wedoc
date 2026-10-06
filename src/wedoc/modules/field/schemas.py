"""Request schemas for /api/table/:tableId/field — ports packages/openapi/src/field."""

from typing import Any

from pydantic import field_validator

from ...core.validation import ZodModel, ZodNullableStr
from ..table.schemas import FieldTypeStr


class FieldCreateBody(ZodModel):
    type: FieldTypeStr
    name: str | None = None
    id: str | None = None
    description: ZodNullableStr = None
    dbFieldName: str | None = None
    unique: bool | None = None
    notNull: bool | None = None
    isLookup: bool | None = None
    lookupOptions: dict[str, Any] | None = None
    options: dict[str, Any] | None = None

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, v: Any) -> Any:
        # createFieldRoSchema.name = z.string().min(1).optional(); the service
        # assigns a type-based default when it is omitted.
        if v is None or not isinstance(v, str):
            return v
        trimmed = v.strip()
        if len(trimmed) < 1:
            raise ValueError("Too small: expected string to have >=1 characters")
        return trimmed


class FieldPatchBody(ZodModel):
    name: str | None = None
    description: ZodNullableStr = None
    dbFieldName: str | None = None

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, v: Any) -> Any:
        if v is None or not isinstance(v, str):
            return v
        trimmed = v.strip()
        if len(trimmed) < 1:
            raise ValueError("Too small: expected string to have >=1 characters")
        return trimmed


class FieldConvertBody(ZodModel):
    type: FieldTypeStr
    name: str | None = None
    description: ZodNullableStr = None
    dbFieldName: str | None = None
    unique: bool | None = None
    notNull: bool | None = None
    isLookup: bool | None = None
    lookupOptions: dict[str, Any] | None = None
    options: dict[str, Any] | None = None


class DuplicateFieldBody(ZodModel):
    name: str
    viewId: str | None = None

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, v: Any) -> Any:
        if not isinstance(v, str):
            return v
        trimmed = v.strip()
        if len(trimmed) < 1:
            raise ValueError("Too small: expected string to have >=1 characters")
        return trimmed
