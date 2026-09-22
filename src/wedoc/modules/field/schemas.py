"""Request schemas for /api/table/:tableId/field — ports packages/openapi/src/field."""

from typing import Any

from pydantic import field_validator

from ...core.validation import ZodModel, ZodNullableStr
from ..table.schemas import FieldTypeStr


class FieldCreateBody(ZodModel):
    type: FieldTypeStr
    name: str
    id: str | None = None
    description: ZodNullableStr = None
    dbFieldName: str | None = None
    unique: bool | None = None
    notNull: bool | None = None
    isPrimary: bool | None = None
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


class DuplicateFieldBody(ZodModel):
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
