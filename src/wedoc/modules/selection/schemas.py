"""Request schemas for /api/table/:tableId/selection — ports packages/openapi/src/selection.

Mirrors range.ts (rangesRoSchema), paste.ts (pasteRoSchema), temporary-paste.ts,
id-mutation.ts (selectionIdScopeSchema / selectionRecordIdScopeSchema and the
copy/clear/paste/delete by-id RO schemas) and id.ts (selectionIdsSchema /
selectionIdsRoSchema, the schema the by-id stream endpoints validate against).
"""

from typing import Annotated, Any, Self

from pydantic import AfterValidator, ConfigDict, field_validator, model_validator

from ...core.validation import ZodEnumStr, ZodModel


def _starts_with(prefix: str, value: Any) -> Any:
    if not isinstance(value, str) or not value.startswith(prefix):
        raise ValueError(f'Invalid string: must start with "{prefix}"')
    return value


def _record_id(value: Any) -> Any:
    return _starts_with("rec", value)


def _field_id(value: Any) -> Any:
    return _starts_with("fld", value)


RecordId = Annotated[str, AfterValidator(_record_id)]
FieldId = Annotated[str, AfterValidator(_field_id)]


def _fields_min_one(value: Any) -> Any:
    if isinstance(value, list) and len(value) < 1:
        raise ValueError("Too small: expected array to have >=1 items")
    return value


def _ranges_min_one(value: Any) -> Any:
    if isinstance(value, list) and len(value) < 1:
        raise ValueError("The range parameter must be a valid 2D array with even length.")
    return value


FieldIdsMin1 = Annotated[list[FieldId], AfterValidator(_fields_min_one)]
# a range is a list of [col, row] number pairs; each element must be a 2-number
# tuple so a malformed shape reports the reference's zod error (not a 500).
Ranges = Annotated[list[tuple[int, int]], AfterValidator(_ranges_min_one)]
PasteContent = str | list[list[Any]]

RangeTypeStr = ZodEnumStr(["rows", "columns"])
IdReturnTypeStr = ZodEnumStr(["recordId", "fieldId", "all"])


class SelectionRecordIdScope(ZodModel):
    recordIds: list[RecordId] | None = None
    excludeRecordIds: list[RecordId] | None = None

    @model_validator(mode="after")
    def _exclusive(self) -> Self:
        if self.recordIds is not None and self.excludeRecordIds is not None:
            raise ValueError("recordIds and excludeRecordIds cannot be used together")
        return self


class SelectionIdScope(SelectionRecordIdScope):
    fieldIds: FieldIdsMin1 | None = None


class SelectionIds(ZodModel):
    # id.ts:5-22 superRefine runs after field parse; declaring recordIds last lets
    # its validator read the already-parsed allRecords from info.data.
    model_config = ConfigDict(extra="ignore", validate_default=True)

    allRecords: bool | None = None
    allFields: bool | None = None
    excludedRecordIds: list[RecordId] | None = None
    excludedFieldIds: list[FieldId] | None = None
    fieldIds: list[FieldId] | None = None
    recordIds: list[RecordId] | None = None

    @field_validator("recordIds", mode="after")
    @classmethod
    def _record_ids_required(cls, value: Any, info: Any) -> Any:
        if not info.data.get("allRecords") and value is None:
            raise ValueError("recordIds is required unless allRecords is true")
        return value


class SelectionIdMutationBody(ZodModel):
    viewId: str | None = None
    projection: list[FieldId] | None = None
    selection: SelectionIdScope


class PasteByIdBody(SelectionIdMutationBody):
    content: PasteContent
    header: list[dict[str, Any]] | None = None


class DeleteByIdBody(ZodModel):
    viewId: str | None = None
    selection: SelectionRecordIdScope


class SelectionIdsBody(ZodModel):
    viewId: str | None = None
    projection: list[FieldId] | None = None
    selection: SelectionIds


class PasteByIdStreamBody(SelectionIdsBody):
    content: PasteContent
    header: list[dict[str, Any]] | None = None


class RangesRoBody(ZodModel):
    viewId: str | None = None
    projection: list[FieldId] | None = None
    ranges: Ranges
    type: RangeTypeStr | None = None


class PasteRoBody(RangesRoBody):
    content: PasteContent
    header: list[dict[str, Any]] | None = None


class TemporaryPasteBody(ZodModel):
    viewId: str | None = None
    projection: list[FieldId] | None = None
    ranges: Ranges
    content: PasteContent
    header: list[dict[str, Any]] | None = None


class RangeToIdReturnType(ZodModel):
    returnType: IdReturnTypeStr
