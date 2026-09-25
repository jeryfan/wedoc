"""Template request schemas — ports packages/openapi/src/template."""

from typing import Annotated, Any

from pydantic import field_validator

from ...core.validation import ZodEnumStr, ZodModel, ZodNullable

PositionStr = ZodEnumStr(["before", "after"])


class TemplateListQueryRo(ZodModel):
    skip: int | None = None
    take: int | None = None

    @field_validator("skip", "take", mode="before")
    @classmethod
    def _coerce(cls, value: Any) -> Any:
        if isinstance(value, str) and value != "":
            try:
                return int(float(value))
            except ValueError:
                return value
        return value


class TemplateQueryRo(ZodModel):
    featured: bool | None = None
    categoryId: str | None = None
    skip: int | None = None
    take: int | None = None
    search: str | None = None

    @field_validator("featured", mode="before")
    @classmethod
    def _stringbool(cls, value: Any) -> Any:
        if isinstance(value, str):
            if value.lower() in ("true", "1", "yes", "on"):
                return True
            if value.lower() in ("false", "0", "no", "off"):
                return False
        return value

    @field_validator("skip", "take", mode="before")
    @classmethod
    def _coerce(cls, value: Any) -> Any:
        if isinstance(value, str) and value != "":
            try:
                return int(float(value))
            except ValueError:
                return value
        return value


class CreateTemplateRo(ZodModel):
    name: str | None = None
    description: str | None = None
    category: str | None = None


class UpdateTemplateRo(ZodModel):
    name: str | None = None
    description: str | None = None
    categoryId: list[str] | None = None
    cover: Annotated[dict[str, Any] | None, ZodNullable()] = None
    isPublished: bool | None = None
    featured: bool | None = None
    isSystem: bool | None = None
    baseId: str | None = None
    markdownDescription: str | None = None


class CreateTemplateCategoryRo(ZodModel):
    name: str


class UpdateTemplateCategoryRo(ZodModel):
    name: str


class UpdateOrderRo(ZodModel):
    anchorId: str
    position: PositionStr
