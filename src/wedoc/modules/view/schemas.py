"""Request schemas for /api/table/:tableId/view — ports packages/openapi/src/view."""

from typing import Annotated, Any

from pydantic import AfterValidator, ConfigDict, Field, StrictBool, ValidationInfo, field_validator

from ...core.validation import (
    ZodEnumStr,
    ZodExpected,
    ZodModel,
    ZodNullable,
    ZodNullableStr,
    zod_validate,
)

ViewTypeStr = ZodEnumStr(["grid", "calendar", "kanban", "form", "gallery", "plugin"])
PositionStr = ZodEnumStr(["before", "after"])

# filterSchema / sortSchema are z.object(...).nullable(): the key is required
# but the value may be null, and a missing key reports "expected object".
NullableObject = Annotated[dict[str, Any] | None, ZodNullable(), ZodExpected("object")]
# groupSchema is groupItemSchema.array().nullable(): required key, nullable array.
NullableItemArray = Annotated[list[dict[str, Any]] | None, ZodNullable()]


def _record_ids_max(value: list[str]) -> list[str]:
    if len(value) > 1000:
        raise ValueError("Too big: expected array to have <=1000 items")
    return value


RecordIds = Annotated[list[str], AfterValidator(_record_ids_max)]


class ViewCreateBody(ZodModel):
    name: str | None = None
    type: ViewTypeStr
    description: ZodNullableStr = None
    order: float | None = None
    options: dict[str, Any] | None = Field(default=None, validate_default=True)
    sort: dict[str, Any] | None = None
    filter: dict[str, Any] | None = None
    group: list[dict[str, Any]] | None = None
    isLocked: bool | None = None
    columnMeta: dict[str, Any] | None = None

    @field_validator("options", mode="after")
    @classmethod
    def _plugin_options_required(
        cls, value: dict[str, Any] | None, info: ValidationInfo
    ) -> dict[str, Any] | None:
        # viewRoSchema.superRefine: plugin views parse options with
        # pluginViewOptionSchema (non-optional), reporting the first issue at
        # path ['options']: a missing options object -> object type, and (since
        # pluginId is the first required key) an options object without it ->
        # string type. pluginId is the field the install path consumes.
        if info.data.get("type") != "plugin":
            return value
        if value is None:
            raise ValueError("Invalid input: expected object, received undefined")
        if value.get("pluginId") is None:
            raise ValueError("Invalid input: expected string, received undefined")
        return value


class ViewNameBody(ZodModel):
    name: str


class ViewDescriptionBody(ZodModel):
    description: str


class ViewLockedBody(ZodModel):
    isLocked: Annotated[StrictBool | None, ZodExpected("boolean")] = None


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
    filter: NullableObject


class ViewSortBody(ZodModel):
    sort: NullableObject


class ViewGroupBody(ZodModel):
    group: NullableItemArray


class ViewOptionsBody(ZodModel):
    # viewOptionsRoSchema = z.object({ options: viewOptionsSchema }): options is
    # required and must be an object; per-view-type strictness is enforced in the
    # service against the view's actual type.
    options: Annotated[dict[str, Any], ZodExpected("object")]


class _StrictViewOptions(ZodModel):
    model_config = ConfigDict(extra="forbid")


class GridViewOptions(_StrictViewOptions):
    rowHeight: str | None = None
    fieldNameDisplayLines: float | None = None
    frozenColumnCount: float | None = None
    frozenFieldId: str | None = None


class KanbanViewOptions(_StrictViewOptions):
    stackFieldId: str | None = None
    coverFieldId: str | None = None
    isCoverFit: bool | None = None
    isFieldNameHidden: bool | None = None
    isEmptyStackHidden: bool | None = None


class GalleryViewOptions(_StrictViewOptions):
    coverFieldId: str | None = None
    isCoverFit: bool | None = None
    isFieldNameHidden: bool | None = None


class CalendarViewOptions(_StrictViewOptions):
    startDateFieldId: str | None = None
    endDateFieldId: str | None = None
    titleFieldId: str | None = None
    colorConfig: dict[str, Any] | None = None


class FormViewOptions(_StrictViewOptions):
    coverUrl: str | None = None
    logoUrl: str | None = None
    submitLabel: str | None = None


class PluginViewOptions(_StrictViewOptions):
    pluginId: str
    pluginInstallId: str
    pluginLogo: str


_VIEW_OPTION_SCHEMAS: dict[str, type[ZodModel]] = {
    "grid": GridViewOptions,
    "kanban": KanbanViewOptions,
    "gallery": GalleryViewOptions,
    "calendar": CalendarViewOptions,
    "form": FormViewOptions,
    "plugin": PluginViewOptions,
}


def validate_view_options(view_type: str, options: dict[str, Any]) -> None:
    # ref validateOptionsType: options must satisfy the view-type-specific strict
    # schema (unknown keys and keys not valid for the type are rejected -> 400).
    schema = _VIEW_OPTION_SCHEMAS.get(view_type)
    if schema is not None:
        zod_validate(schema, options)


class ColumnMetaItem(ZodModel):
    fieldId: str
    columnMeta: dict[str, Any]


class ViewOrderBody(ZodModel):
    anchorId: str
    position: PositionStr


class RecordOrderBody(ZodModel):
    anchorId: str
    position: PositionStr
    recordIds: RecordIds


class ManualSortBody(ZodModel):
    sortObjs: list[dict[str, Any]]
