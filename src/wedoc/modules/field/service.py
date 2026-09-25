"""field domain service — ports features/field (field-open-api.service.ts v2 flows).

Only the REST-visible surface is ported: field meta CRUD against the physical
data table (add column on create). convert + plan routes land in a later
slice; link/lookup dependent routes stay empty until link fields exist.
"""

import json
import math
import re
from datetime import UTC, datetime
from typing import Any

from ...core import cls
from ...core.errors import ApiError, HttpErrorCode
from ...core.ids import IdPrefix, new_id
from ...db import provider as ddl
from ...formula import (
    FormulaError,
    parsed_value_type,
    reference_field_ids,
)
from ...formula import (
    parse as parse_formula,
)
from ..table import repository as table_repository
from ..table.service import CELL_VALUE_TYPES, COMPUTED_FIELD_TYPES, DB_FIELD_TYPES
from . import link_field, repository
from .schemas import DuplicateFieldBody, FieldConvertBody, FieldCreateBody, FieldPatchBody

# select-field color auto-assignment order (Colors enum declaration order).
COLORS = [
    "blueLight2",
    "blueLight1",
    "blueBright",
    "blue",
    "blueDark1",
    "cyanLight2",
    "cyanLight1",
    "cyanBright",
    "cyan",
    "cyanDark1",
    "grayLight2",
    "grayLight1",
    "grayBright",
    "gray",
    "grayDark1",
    "greenLight2",
    "greenLight1",
    "greenBright",
    "green",
    "greenDark1",
    "orangeLight2",
    "orangeLight1",
    "orangeBright",
    "orange",
    "orangeDark1",
    "pinkLight2",
    "pinkLight1",
    "pinkBright",
    "pink",
    "pinkDark1",
    "purpleLight2",
    "purpleLight1",
    "purpleBright",
    "purple",
    "purpleDark1",
    "redLight2",
    "redLight1",
    "redBright",
    "red",
    "redDark1",
    "tealLight2",
    "tealLight1",
    "tealBright",
    "teal",
    "tealDark1",
    "yellowLight2",
    "yellowLight1",
    "yellowBright",
    "yellow",
    "yellowDark1",
]

_DB_FIELD_NAME_RE = re.compile(r"^\w{1,63}$")
_DERIVED_DB_NAME_RE = re.compile(r"\W")

# scalar types this slice converts between; link/lookup/rollup/formula need the
# relational/computed migration engine and are deferred with a clear 400.
CONVERT_SUPPORTED_TYPES = {
    "singleLineText",
    "longText",
    "number",
    "singleSelect",
    "multipleSelect",
    "date",
    "checkbox",
    "rating",
    "user",
}
# targets whose existing cells are migrated through the typecast engine. user is
# best-effort: rebuilding collaborator cells needs the collaborator service, so
# its cells are cleared instead of guessed.
_CELL_MIGRATION_TARGETS = CONVERT_SUPPORTED_TYPES - {"user"}


def _scalar_convertible(field: dict[str, Any]) -> bool:
    return (
        field["type"] in CONVERT_SUPPORTED_TYPES
        and not field.get("is_lookup")
        and not field.get("is_computed")
    )


def _convert_not_supported(source_type: str, target_type: str) -> ApiError:
    return ApiError(
        f"Converting field type from {source_type} to {target_type} is not supported",
        HttpErrorCode.VALIDATION_ERROR,
        {
            "localization": {
                "i18nKey": "httpErrors.field.unsupportedFieldConvert",
                "context": {"sourceType": source_type, "targetType": target_type},
            }
        },
    )


def _js_round(value: float) -> int:
    # rating coercion clamps Math.round(num); JS rounds .5 toward +Inf whereas
    # Python's round() is banker's rounding.
    return math.floor(value + 0.5)


def _link_cell_title(value: Any) -> str | None:
    """Denormalized link cell ({id,title} or a list) rendered as a title string."""
    if value is None:
        return None
    items = value if isinstance(value, list) else [value]
    titles = [
        str(item["title"])
        for item in items
        if isinstance(item, dict) and item.get("title") is not None
    ]
    return ", ".join(titles) if titles else None


def _table_missing(table_id: str) -> ApiError:
    return ApiError(
        "Table not found",
        HttpErrorCode.NOT_FOUND,
        {"domainCode": "table.not_found", "domainTags": ["not-found"]},
    )


def _field_not_found(field_id: str) -> ApiError:
    return ApiError(f"Field {field_id} not found", HttpErrorCode.NOT_FOUND)


def _field_not_found_plain() -> ApiError:
    return ApiError(
        "Field not found",
        HttpErrorCode.NOT_FOUND,
        {"domainCode": "not_found", "domainTags": ["not-found"]},
    )


def _name_conflict() -> ApiError:
    return ApiError(
        "Field names must be unique",
        HttpErrorCode.VALIDATION_ERROR,
        {"domainCode": "conflict", "domainTags": ["conflict"]},
    )


def _invalid_db_field_name() -> ApiError:
    return ApiError(
        'Validation error: Invalid name format at "dbFieldName"',
        HttpErrorCode.VALIDATION_ERROR,
    )


def _link_relationship_invalid() -> ApiError:
    return ApiError(
        'Validation error: Validation error: Invalid option: expected one of '
        '"oneOne"|"manyMany"|"oneMany"|"manyOne" at "relationship" at "options"',
        HttpErrorCode.VALIDATION_ERROR,
    )


def _foreign_table_id_required() -> ApiError:
    return ApiError(
        'Validation error: ForeignTableId is required when type is link at "options"; '
        'Validation error: Invalid input: expected string, received undefined '
        'at "foreignTableId" at "options"',
        HttpErrorCode.VALIDATION_ERROR,
    )


def _foreign_tables_not_found(missing: list[str]) -> ApiError:
    return ApiError(
        f"Foreign tables not found: {', '.join(missing)}",
        HttpErrorCode.NOT_FOUND,
        {
            "domainCode": "not_found",
            "domainTags": ["not-found"],
            "details": {"missingForeignTableIds": missing},
        },
    )


def _pick_link_lookup_field(fields: list[dict[str, Any]]) -> str:
    label = next((f["id"] for f in fields if f["name"] == "Label"), None)
    if label:
        return label
    primary = next((f["id"] for f in fields if f.get("is_primary")), None)
    return primary or fields[0]["id"]


def _link_field_id_invalid(link_field_id: str) -> ApiError:
    return ApiError(
        f"linkFieldId {link_field_id} is invalid",
        HttpErrorCode.VALIDATION_ERROR,
        {"localization": {"i18nKey": "httpErrors.field.linkFieldIdInvalid",
                          "context": {"linkFieldId": link_field_id}}},
    )


def _foreign_table_id_invalid(foreign_table_id: str | None) -> ApiError:
    return ApiError(
        f"foreignTableId {foreign_table_id} is invalid",
        HttpErrorCode.VALIDATION_ERROR,
        {"localization": {"i18nKey": "httpErrors.field.foreignTableIdInvalid",
                          "context": {"foreignTableId": foreign_table_id}}},
    )


def _lookup_field_id_invalid(lookup_field_id: str) -> ApiError:
    return ApiError(
        f"Lookup field {lookup_field_id} is invalid",
        HttpErrorCode.VALIDATION_ERROR,
        {"localization": {"i18nKey": "httpErrors.field.lookupFieldIdInvalid",
                          "context": {"lookupFieldId": lookup_field_id}}},
    )


_DEFAULT_FORMATTING = {"number": {"type": "decimal", "precision": 2}}

# The reference defaults a formula field's timeZone to the server's resolved
# timezone (Intl...). That is environment-specific; wedoc uses a deterministic
# default, overridable via options.timeZone.
_FORMULA_TIMEZONE = "UTC"


def _formula_parse_error(expression: str | None, exc: Exception) -> ApiError:
    return ApiError(
        f"formula expression {expression} parse error: {exc}",
        HttpErrorCode.VALIDATION_ERROR,
        {"localization": {"i18nKey": "httpErrors.field.formulaExpressionParseError"}},
    )


def _formula_type_error(expression: str | None, exc: Exception) -> ApiError:
    return ApiError(
        f"Parse formula expression {expression} error: {exc}",
        HttpErrorCode.VALIDATION_ERROR,
        {"localization": {"i18nKey": "httpErrors.field.formulaExpressionParseError"}},
    )


def _formula_reference_not_found(missing: list[str]) -> ApiError:
    joined = ", ".join(missing)
    looks_like_names = any(not m.startswith("fld") or len(m) != 19 for m in missing)
    if looks_like_names:
        message = (
            f"Formula references not found: {joined}. Formulas must use field IDs "
            "(fldXXXXXXXXXXXXXXXX format), not field names."
        )
        i18n_key = "httpErrors.field.formulaReferenceNotFieldId"
    else:
        message = (
            f"Formula field references not found: {joined}. These field IDs do not "
            "exist in the table."
        )
        i18n_key = "httpErrors.field.formulaReferenceNotFound"
    return ApiError(
        message,
        HttpErrorCode.VALIDATION_ERROR,
        {"localization": {"i18nKey": i18n_key, "context": {"fieldIds": joined}}},
    )


def _default_formula_formatting(cell_value_type: str) -> dict[str, Any] | None:
    if cell_value_type == "number":
        return {"type": "decimal", "precision": 2}
    if cell_value_type == "dateTime":
        return {"date": "YYYY-MM-DD", "time": "None", "timeZone": _FORMULA_TIMEZONE}
    return None



def _rollup_return_type(
    expression: str, source_cvt: str, source_multiple: bool
) -> tuple[str, bool]:
    # ports the rollup function return-type table (getParsedValueType), covering
    # ROLLUP_FUNCTIONS; max/min keep the source type, array_* stay multi-valued.
    fn = expression.split("(", 1)[0].strip().lower()
    if fn in ("countall", "counta", "count", "sum", "average"):
        return ("number", False)
    if fn in ("max", "min"):
        return (source_cvt, False)
    if fn in ("and", "or", "xor"):
        return ("boolean", False)
    if fn in ("array_join", "concatenate"):
        return ("string", False)
    if fn in ("array_unique", "array_compact"):
        return (source_cvt, True)
    return ("string", False)


def _derive_db_field_type(field_type: str, cell_value_type: str, is_multiple: bool) -> str:
    # ports get-db-field-type.ts.
    if is_multiple:
        return "JSON"
    if field_type in ("link", "user", "attachment", "button", "createdBy", "lastModifiedBy"):
        return "JSON"
    if field_type == "autoNumber":
        return "INTEGER"
    return {
        "number": "REAL",
        "dateTime": "DATETIME",
        "boolean": "BOOLEAN",
        "string": "TEXT",
    }.get(cell_value_type, "TEXT")


def _lookup_formatting_show_as(
    request_options: dict[str, Any] | None,
    source_options: dict[str, Any] | None,
    cell_value_type: str,
) -> dict[str, Any]:
    # ports prepareFormattingShowAs: mirror the source field's structural options,
    # letting the request override formatting/showAs; drop keys that resolve to None.
    source = dict(source_options) if isinstance(source_options, dict) else {}
    req = request_options if isinstance(request_options, dict) else {}
    formatting = (
        req["formatting"] if "formatting" in req
        else source.get("formatting", _DEFAULT_FORMATTING.get(cell_value_type))
    )
    show_as = req["showAs"] if "showAs" in req else source.get("showAs")
    result = dict(source)
    if formatting is not None:
        result["formatting"] = formatting
    else:
        result.pop("formatting", None)
    if show_as is not None:
        result["showAs"] = show_as
    else:
        result.pop("showAs", None)
    return result


def _symmetric_field_name(base: str, existing: set[str]) -> str:
    # v2 Table.generateFieldName: base, then "<base> (linked)", "(linked 2)"...
    if base not in existing:
        return base
    for index in range(1, 101):
        suffix = " (linked)" if index == 1 else f" (linked {index})"
        candidate = f"{base}{suffix}"
        if candidate not in existing:
            return candidate
    return f"{base} (linked {new_id(IdPrefix.FIELD)})"


def _normalize_options(field_type: str, options: dict[str, Any] | None) -> dict[str, Any]:
    options = dict(options) if options else {}
    if field_type == "number":
        if "precision" in options:
            options["formatting"] = {
                "type": "decimal",
                "precision": options.pop("precision"),
            }
        options.setdefault("formatting", {"type": "decimal", "precision": 2})
    if field_type == "button":
        options.setdefault("label", "Button")
        options.setdefault("color", "teal")
    if field_type in ("singleSelect", "multipleSelect"):
        used_colors: set[str] = set()
        choices = []
        for choice in options.get("choices", []):
            color = choice.get("color")
            if not color:
                color = next((c for c in COLORS if c not in used_colors), COLORS[0])
            used_colors.add(color)
            choices.append(
                {
                    "id": choice.get("id") or new_id(IdPrefix.CHOICE, 8),
                    "name": choice["name"],
                    "color": color,
                }
            )
        if choices or "choices" in options:
            options["choices"] = choices
    if field_type == "rating":
        options = {
            "icon": options.get("icon", "star"),
            "color": options.get("color", "yellowBright"),
            "max": options.get("max", 5),
        }
    return options


def _field_vo(row: dict[str, Any]) -> dict[str, Any]:
    vo: dict[str, Any] = {"id": row["id"], "name": row["name"]}
    if row.get("description") is not None:
        vo["description"] = row["description"]
    vo["dbFieldName"] = row["db_field_name"]
    if row.get("is_primary"):
        vo["isPrimary"] = True
    if row.get("not_null"):
        vo["notNull"] = True
    vo["unique"] = bool(row.get("unique"))
    vo["cellValueType"] = row["cell_value_type"]
    vo["dbFieldType"] = row["db_field_type"]
    vo["type"] = row["type"]
    if row.get("is_multiple_cell_value"):
        vo["isMultipleCellValue"] = True
    if row["type"] in COMPUTED_FIELD_TYPES or row.get("is_computed"):
        vo["isComputed"] = True
    if row.get("is_lookup"):
        vo["isLookup"] = True
    if row.get("lookup_options"):
        vo["lookupOptions"] = json.loads(row["lookup_options"])
    vo["options"] = json.loads(row["options"] or "{}")
    return vo


def _dedup_name(name: str, taken: set[str]) -> str:
    if name not in taken:
        return name
    index = 2
    while f"{name} {index}" in taken:
        index += 1
    return f"{name} {index}"


def _dedup_db_name(db_name: str, taken: set[str]) -> str:
    if db_name not in taken:
        return db_name
    index = 2
    while f"{db_name}_{index}" in taken:
        index += 1
    return f"{db_name}_{index}"


class FieldService:
    async def _load_table(self, table_id: str) -> dict[str, Any]:
        table = await repository.get_table_meta_by_id(table_id)
        if table is None or table["deleted_time"] is not None:
            raise _table_missing(table_id)
        return table

    async def _load_field(self, table_id: str, field_id: str) -> dict[str, Any]:
        field = await repository.get_field_row(table_id, field_id)
        if field is None:
            raise _field_not_found(field_id)
        return field

    def _derive_db_field_name(
        self,
        raw: FieldCreateBody | FieldPatchBody,
        final_name: str,
        taken: set[str],
    ) -> str:
        if isinstance(raw, FieldPatchBody) and raw.dbFieldName is None:
            return ""
        if raw.dbFieldName is not None:
            if not _DB_FIELD_NAME_RE.match(raw.dbFieldName):
                raise _invalid_db_field_name()
            return _dedup_db_name(raw.dbFieldName, taken)
        derived = _DERIVED_DB_NAME_RE.sub("_", final_name)
        return _dedup_db_name(derived, taken)

    async def create_field(self, table_id: str, body: FieldCreateBody) -> dict[str, Any]:
        vo = await self._create_field(table_id, body)
        from ...realtime.broadcast import broadcast_action_trigger, broadcast_field_create
        from ..undo_redo.stack import capture_operation

        await broadcast_field_create(table_id, vo)
        await broadcast_action_trigger(
            table_id,
            [{"actionKey": "addField", "payload": {"tableId": table_id, "fieldId": vo["id"]}}],
        )
        await capture_operation(
            table_id,
            {"name": "createFields", "params": {"tableId": table_id}, "result": {"fields": [vo]}},
        )
        return vo

    async def restore_field(self, table_id: str, field_id: str) -> dict[str, Any] | None:
        """Undo of a field delete: clear the soft-delete tombstone (the physical
        column and its data are retained by delete_field) and re-announce it."""
        field = await repository.get_field_row(table_id, field_id, include_deleted=True)
        if field is None:
            return None
        row = await repository.update_field_row(
            field_id,
            {
                "deleted_time": None,
                "version": field["version"] + 1,
                "last_modified_time": datetime.now(UTC).replace(tzinfo=None),
                "last_modified_by": cls.get("user.id"),
            },
        )
        vo = _field_vo(row or field)
        from ...realtime.broadcast import broadcast_action_trigger, broadcast_field_create

        await broadcast_field_create(table_id, vo)
        await broadcast_action_trigger(
            table_id,
            [{"actionKey": "addField", "payload": {"tableId": table_id, "fieldId": field_id}}],
        )
        return vo

    async def _create_field(self, table_id: str, body: FieldCreateBody) -> dict[str, Any]:
        table = await self._load_table(table_id)
        if body.isLookup:
            return await self._create_lookup_field(table, body)
        if body.type == "link":
            return await self._create_link_field(table, body)
        if body.type == "rollup":
            return await self._create_rollup_field(table, body)
        if body.type == "formula":
            return await self._create_formula_field(table, body)
        base_id = table["base_id"]
        user_id = cls.get("user.id")
        now = datetime.now(UTC).replace(tzinfo=None)

        siblings = await table_repository.list_field_rows(table_id)
        taken_names = {f["name"] for f in siblings}
        taken_db_names = {f["db_field_name"] for f in siblings}
        name = _dedup_name(body.name, taken_names)
        db_field_name = self._derive_db_field_name(body, name, taken_db_names)

        if body.notNull:
            record_count = await repository.count_data_rows(base_id, table_id)
            if record_count > 0:
                field_id = body.id or new_id(IdPrefix.FIELD)
                raise ApiError(
                    f'Cannot mark field "{name}" as required because existing records '
                    "contain empty values.",
                    HttpErrorCode.VALIDATION_ERROR,
                    {
                        "domainCode": "validation.field.required_existing_values",
                        "domainTags": ["validation"],
                        "details": {"fieldId": field_id, "fieldName": name},
                        "localization": {
                            "i18nKey": "httpErrors.custom.fieldRequiredExistingValues",
                            "context": {"fieldName": name},
                        },
                    },
                )

        field_id = body.id or new_id(IdPrefix.FIELD)
        options = _normalize_options(body.type, body.options)
        row = {
            "id": field_id,
            "name": name,
            "description": body.description,
            "type": body.type,
            "db_field_name": db_field_name,
            "db_field_type": DB_FIELD_TYPES[body.type],
            "cell_value_type": CELL_VALUE_TYPES[body.type],
            "is_multiple_cell_value": body.type in ("multipleSelect", "attachment", "user"),
            "is_primary": body.isPrimary or None,
            "not_null": body.notNull or None,
            "unique": body.unique or False,
            "options": json.dumps(options, separators=(",", ":")),
            "table_id": table_id,
            "order": await table_repository.max_field_order(table_id) + 1,
            "version": 1,
            "created_by": user_id,
            "last_modified_time": now,
            "last_modified_by": user_id,
        }
        await table_repository.execute_data_ddl(
            [ddl.add_field_column_sql(base_id, table_id, db_field_name, body.type)]
        )
        await table_repository.insert_field_rows([row])
        return _field_vo(row)

    # ---- link fields --------------------------------------------------------

    async def _create_link_field(
        self, table: dict[str, Any], body: FieldCreateBody
    ) -> dict[str, Any]:
        table_id = table["id"]
        base_id = table["base_id"]
        user_id = cls.get("user.id")
        now = datetime.now(UTC).replace(tzinfo=None)
        options_ro = dict(body.options or {})

        relationship = options_ro.get("relationship")
        if relationship not in link_field.RELATIONSHIPS:
            raise _link_relationship_invalid()
        foreign_table_id = options_ro.get("foreignTableId")
        if not isinstance(foreign_table_id, str) or not foreign_table_id:
            raise _foreign_table_id_required()

        foreign_table = await repository.get_table_meta_by_id(foreign_table_id)
        if foreign_table is None or foreign_table["deleted_time"] is not None:
            raise _foreign_tables_not_found([foreign_table_id])

        # same-base links drop the (optional) baseId; only cross-base keeps it.
        base_id_opt = options_ro.get("baseId")
        if base_id_opt is not None and base_id_opt == base_id:
            base_id_opt = None

        foreign_fields = await table_repository.list_field_rows(foreign_table_id)
        lookup_field_id = options_ro.get("lookupFieldId") or _pick_link_lookup_field(
            foreign_fields
        )
        self_fields = await table_repository.list_field_rows(table_id)
        self_primary_id = next(
            (f["id"] for f in self_fields if f.get("is_primary")), None
        )

        field_id = body.id or new_id(IdPrefix.FIELD)
        is_one_way = bool(options_ro.get("isOneWay"))
        symmetric_field_id = None if is_one_way else new_id(IdPrefix.FIELD)

        options = link_field.build_link_options(
            options_ro=options_ro,
            field_id=field_id,
            symmetric_field_id=symmetric_field_id,
            lookup_field_id=lookup_field_id,
            self_db_table_name=table["db_table_name"],
            foreign_db_table_name=foreign_table["db_table_name"],
            base_id=base_id_opt,
        )
        is_multiple = link_field.is_multi_value_link(relationship)

        taken_names = {f["name"] for f in self_fields}
        taken_db_names = {f["db_field_name"] for f in self_fields}
        name = _dedup_name(body.name or foreign_table["name"], taken_names)
        db_field_name = self._derive_db_field_name(body, name, taken_db_names)

        row = self._link_field_row(
            field_id, name, body.description, db_field_name, options, is_multiple,
            table_id, await table_repository.max_field_order(table_id) + 1,
            user_id, now,
        )
        await table_repository.execute_data_ddl(
            [ddl.add_field_column_sql(base_id, table_id, db_field_name, "link")]
        )
        await table_repository.execute_data_ddl(
            ddl.link_relation_ddl(
                options, table["db_table_name"], foreign_table["db_table_name"]
            )
        )
        await table_repository.insert_field_rows([row])

        if symmetric_field_id is not None:
            await self._create_symmetric_field(
                table, foreign_table, foreign_fields, options,
                field_id, self_primary_id, user_id, now,
            )
        return _field_vo(row)

    async def _create_symmetric_field(
        self,
        table: dict[str, Any],
        foreign_table: dict[str, Any],
        foreign_fields: list[dict[str, Any]],
        main_options: dict[str, Any],
        main_field_id: str,
        self_primary_id: str | None,
        user_id: str,
        now: datetime,
    ) -> None:
        foreign_table_id = foreign_table["id"]
        symmetric_field_id = main_options["symmetricFieldId"]
        taken_names = {f["name"] for f in foreign_fields}
        taken_db_names = {f["db_field_name"] for f in foreign_fields}
        sym_name = _symmetric_field_name(table["name"], taken_names)
        sym_db_field_name = _dedup_db_name(
            _DERIVED_DB_NAME_RE.sub("_", ddl.convert_name_to_valid_character(sym_name, 40)),
            taken_db_names,
        )
        sym_options = link_field.build_symmetric_options(
            main_options, table["id"], self_primary_id or "", main_field_id
        )
        sym_is_multiple = link_field.is_multi_value_link(sym_options["relationship"])
        sym_row = self._link_field_row(
            symmetric_field_id, sym_name, None, sym_db_field_name, sym_options,
            sym_is_multiple, foreign_table_id,
            await table_repository.max_field_order(foreign_table_id) + 1, user_id, now,
        )
        await table_repository.execute_data_ddl(
            [ddl.add_field_column_sql(
                foreign_table["base_id"], foreign_table_id, sym_db_field_name, "link"
            )]
        )
        await table_repository.insert_field_rows([sym_row])
        from ...realtime.broadcast import broadcast_field_create

        await broadcast_field_create(foreign_table_id, _field_vo(sym_row))

    async def _resolve_lookup_source(
        self, table_id: str, lookup_ro: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        """Validate + resolve the (link field, link options, source field) for a
        lookup/rollup field (ports prepareLookupOptions)."""
        if not lookup_ro.get("linkFieldId"):
            raise ApiError(
                "lookupOptions is required",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "editor.lookup.lookupOptionsRequired"}},
            )
        link_field = await repository.get_field_row(table_id, lookup_ro["linkFieldId"])
        if link_field is None or link_field["type"] != "link":
            raise _link_field_id_invalid(lookup_ro["linkFieldId"])
        link_options = json.loads(link_field["options"] or "{}")
        if lookup_ro.get("foreignTableId") != link_options.get("foreignTableId"):
            raise _foreign_table_id_invalid(lookup_ro.get("foreignTableId"))
        source_field = await repository.get_field_row(
            link_options["foreignTableId"], lookup_ro["lookupFieldId"]
        )
        if source_field is None:
            raise _lookup_field_id_invalid(lookup_ro["lookupFieldId"])
        return link_field, link_options, source_field

    @staticmethod
    def _enriched_lookup_options(
        lookup_ro: dict[str, Any], link_options: dict[str, Any]
    ) -> dict[str, Any]:
        options: dict[str, Any] = {
            "linkFieldId": lookup_ro["linkFieldId"],
            "lookupFieldId": lookup_ro["lookupFieldId"],
            "foreignTableId": link_options["foreignTableId"],
        }
        # conditional lookup/rollup carries a filter on the foreign records.
        if lookup_ro.get("filter") is not None:
            options["filter"] = lookup_ro["filter"]
        options.update(
            {
                "relationship": link_options["relationship"],
                "fkHostTableName": link_options["fkHostTableName"],
                "selfKeyName": link_options["selfKeyName"],
                "foreignKeyName": link_options["foreignKeyName"],
            }
        )
        return options

    async def _create_lookup_field(
        self, table: dict[str, Any], body: FieldCreateBody
    ) -> dict[str, Any]:
        table_id = table["id"]
        base_id = table["base_id"]
        user_id = cls.get("user.id")
        now = datetime.now(UTC).replace(tzinfo=None)
        lookup_ro = dict(body.lookupOptions or {})
        link_field, link_options, lookup_field = await self._resolve_lookup_source(
            table_id, lookup_ro
        )
        link_field_id = lookup_ro["linkFieldId"]
        # v2 lookup derives the field type from the source field (request `type`
        # is advisory); no type-equality guard.
        field_type = lookup_field["type"]

        is_multiple = bool(link_field.get("is_multiple_cell_value")) or bool(
            lookup_field.get("is_multiple_cell_value")
        )
        cell_value_type = lookup_field["cell_value_type"]
        source_options = json.loads(lookup_field["options"] or "{}")
        options = _lookup_formatting_show_as(body.options, source_options, cell_value_type)
        db_field_type = _derive_db_field_type(field_type, cell_value_type, is_multiple)

        lookup_options = self._enriched_lookup_options(lookup_ro, link_options)

        siblings = await table_repository.list_field_rows(table_id)
        name = _dedup_name(
            body.name or f"{lookup_field['name']} (from {link_field['name']})",
            {f["name"] for f in siblings},
        )
        db_field_name = self._derive_db_field_name(
            body, name, {f["db_field_name"] for f in siblings}
        )
        field_id = body.id or new_id(IdPrefix.FIELD)
        row = {
            "id": field_id,
            "name": name,
            "description": body.description,
            "type": field_type,
            "db_field_name": db_field_name,
            "db_field_type": db_field_type,
            "cell_value_type": cell_value_type,
            "is_multiple_cell_value": is_multiple or None,
            "is_computed": True,
            "is_lookup": True,
            "lookup_linked_field_id": link_field_id,
            "lookup_options": json.dumps(lookup_options, separators=(",", ":")),
            "options": json.dumps(options, separators=(",", ":")),
            "table_id": table_id,
            "order": await table_repository.max_field_order(table_id) + 1,
            "version": 1,
            "created_by": user_id,
            "last_modified_time": now,
            "last_modified_by": user_id,
        }
        await table_repository.execute_data_ddl(
            [ddl.add_column_by_db_type_sql(base_id, table_id, db_field_name, db_field_type)]
        )
        await table_repository.insert_field_rows([row])
        vo = _field_vo(row)
        # ref queues async computation and reports the field as pending on create;
        # the value is materialized on read (see record service lookup resolution).
        vo["isPending"] = True
        return vo

    async def _create_rollup_field(
        self, table: dict[str, Any], body: FieldCreateBody
    ) -> dict[str, Any]:
        table_id = table["id"]
        base_id = table["base_id"]
        user_id = cls.get("user.id")
        now = datetime.now(UTC).replace(tzinfo=None)
        options_ro = dict(body.options or {})
        expression = options_ro.get("expression")
        if not expression:
            raise ApiError(
                "rollup field options is required",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "editor.error.optionsRequired"}},
            )
        lookup_ro = dict(body.lookupOptions or {})
        link_field, link_options, source_field = await self._resolve_lookup_source(
            table_id, lookup_ro
        )
        source_multiple = bool(source_field.get("is_multiple_cell_value")) or bool(
            link_field.get("is_multiple_cell_value")
        )
        cell_value_type, is_multiple = _rollup_return_type(
            expression, source_field["cell_value_type"], source_multiple
        )
        options: dict[str, Any] = {"expression": expression}
        if "timeZone" in options_ro:
            options["timeZone"] = options_ro["timeZone"]
        formatting = options_ro.get("formatting", _DEFAULT_FORMATTING.get(cell_value_type))
        if formatting is not None:
            options["formatting"] = formatting
        if options_ro.get("showAs") is not None:
            options["showAs"] = options_ro["showAs"]
        db_field_type = _derive_db_field_type("rollup", cell_value_type, is_multiple)
        lookup_options = self._enriched_lookup_options(lookup_ro, link_options)

        siblings = await table_repository.list_field_rows(table_id)
        name = _dedup_name(
            body.name or f"{source_field['name']} Rollup (from {link_field['name']})",
            {f["name"] for f in siblings},
        )
        db_field_name = self._derive_db_field_name(
            body, name, {f["db_field_name"] for f in siblings}
        )
        field_id = body.id or new_id(IdPrefix.FIELD)
        row = {
            "id": field_id,
            "name": name,
            "description": body.description,
            "type": "rollup",
            "db_field_name": db_field_name,
            "db_field_type": db_field_type,
            "cell_value_type": cell_value_type,
            "is_multiple_cell_value": is_multiple or None,
            "is_computed": True,
            "lookup_linked_field_id": lookup_ro["linkFieldId"],
            "lookup_options": json.dumps(lookup_options, separators=(",", ":")),
            "options": json.dumps(options, separators=(",", ":")),
            "table_id": table_id,
            "order": await table_repository.max_field_order(table_id) + 1,
            "version": 1,
            "created_by": user_id,
            "last_modified_time": now,
            "last_modified_by": user_id,
        }
        await table_repository.execute_data_ddl(
            [ddl.add_column_by_db_type_sql(base_id, table_id, db_field_name, db_field_type)]
        )
        await table_repository.insert_field_rows([row])
        vo = _field_vo(row)
        vo["isPending"] = True
        return vo

    async def _create_formula_field(
        self, table: dict[str, Any], body: FieldCreateBody
    ) -> dict[str, Any]:
        table_id = table["id"]
        base_id = table["base_id"]
        user_id = cls.get("user.id")
        now = datetime.now(UTC).replace(tzinfo=None)
        options_ro = dict(body.options or {})
        expression = options_ro.get("expression")
        try:
            tree = parse_formula(expression or "")
        except FormulaError as exc:
            raise _formula_parse_error(expression, exc) from exc

        siblings = await table_repository.list_field_rows(table_id)
        field_by_id = {f["id"]: f for f in siblings}
        ref_ids = reference_field_ids(tree)
        missing = [rid for rid in ref_ids if rid not in field_by_id]
        if missing:
            raise _formula_reference_not_found(missing)
        dependencies = {rid: field_by_id[rid] for rid in ref_ids}
        try:
            cell_value_type, is_multiple = parsed_value_type(tree, dependencies)
        except FormulaError as exc:
            raise _formula_type_error(expression, exc) from exc

        options: dict[str, Any] = dict(options_ro)
        formatting = options_ro.get("formatting", _default_formula_formatting(cell_value_type))
        if formatting is not None:
            options["formatting"] = formatting
        else:
            options.pop("formatting", None)
        options["timeZone"] = options_ro.get("timeZone", _FORMULA_TIMEZONE)
        db_field_type = _derive_db_field_type("formula", cell_value_type, is_multiple)

        name = _dedup_name(body.name or "Calculation", {f["name"] for f in siblings})
        db_field_name = self._derive_db_field_name(
            body, name, {f["db_field_name"] for f in siblings}
        )
        field_id = body.id or new_id(IdPrefix.FIELD)
        row = {
            "id": field_id,
            "name": name,
            "description": body.description,
            "type": "formula",
            "db_field_name": db_field_name,
            "db_field_type": db_field_type,
            "cell_value_type": cell_value_type,
            "is_multiple_cell_value": is_multiple or None,
            "is_computed": True,
            "is_primary": body.isPrimary or None,
            "options": json.dumps(options, separators=(",", ":")),
            "table_id": table_id,
            "order": await table_repository.max_field_order(table_id) + 1,
            "version": 1,
            "created_by": user_id,
            "last_modified_time": now,
            "last_modified_by": user_id,
        }
        await table_repository.execute_data_ddl(
            [ddl.add_column_by_db_type_sql(base_id, table_id, db_field_name, db_field_type)]
        )
        await table_repository.insert_field_rows([row])
        vo = _field_vo(row)
        vo["isPending"] = True
        return vo

    @staticmethod
    def _link_field_row(
        field_id: str, name: str, description: str | None, db_field_name: str,
        options: dict[str, Any], is_multiple: bool, table_id: str, order: float,
        user_id: str, now: datetime,
    ) -> dict[str, Any]:
        return {
            "id": field_id,
            "name": name,
            "description": description,
            "type": "link",
            "db_field_name": db_field_name,
            "db_field_type": "JSON",
            "cell_value_type": "string",
            "is_multiple_cell_value": is_multiple or None,
            "options": json.dumps(options, separators=(",", ":")),
            "table_id": table_id,
            "order": order,
            "version": 1,
            "created_by": user_id,
            "last_modified_time": now,
            "last_modified_by": user_id,
        }

    async def get_field(self, table_id: str, field_id: str) -> dict[str, Any]:
        await self._load_table(table_id)
        return _field_vo(await self._load_field(table_id, field_id))

    async def get_filter_link_records(
        self, table_id: str, field_id: str
    ) -> list[dict[str, Any]]:
        # ports field-open-api getFilterLinkRecords: only link/conditionalRollup
        # fields carrying an explicit record-limiting filter return candidates;
        # every other field (and unfiltered link fields) returns [].
        await self._load_table(table_id)
        field = await self._load_field(table_id, field_id)
        if field["type"] == "link" and not field.get("is_lookup"):
            options = json.loads(field["options"] or "{}")
            filter_obj = options.get("filter")
            foreign_table_id = options.get("foreignTableId")
            if foreign_table_id and filter_obj:
                from ..view.service import get_filter_link_records_by_table

                return await get_filter_link_records_by_table(foreign_table_id, filter_obj)
        return []

    async def list_fields(
        self,
        table_id: str,
        projection: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        await self._load_table(table_id)
        rows = await table_repository.list_field_rows(table_id)
        if projection:
            wanted = set(projection)
            rows = [r for r in rows if r["id"] in wanted]
        return [_field_vo(r) for r in rows]

    # ---- realtime socket snapshots -----------------------------------------

    async def socket_snapshot_bulk(
        self, table_id: str, ids: list[str]
    ) -> list[dict[str, Any]]:
        """Return ShareDB field snapshots ``{id, v, type, data}`` for ids."""
        await self._load_table(table_id)
        rows = {r["id"]: r for r in await table_repository.list_field_rows(table_id)}
        snapshots: list[dict[str, Any]] = []
        for field_id in ids:
            row = rows.get(field_id)
            if row is None:
                continue
            snapshots.append(
                {
                    "id": field_id,
                    "v": row["version"],
                    "type": "json0",
                    "data": _field_vo(row),
                }
            )
        return snapshots

    async def socket_doc_ids(
        self, table_id: str, query: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        await self._load_table(table_id)
        rows = await table_repository.list_field_rows(table_id)
        return {"ids": [r["id"] for r in rows]}

    async def update_field(
        self, table_id: str, field_id: str, body: FieldPatchBody
    ) -> dict[str, Any]:
        await self._load_table(table_id)
        field = await self._load_field(table_id, field_id)
        old_vo = _field_vo(field)
        updates: dict[str, Any] = {}

        if body.name is not None and body.name != field["name"]:
            siblings = await table_repository.list_field_rows(table_id)
            if body.name in {f["name"] for f in siblings if f["id"] != field_id}:
                raise _name_conflict()
            updates["name"] = body.name
        if body.description is not None:
            updates["description"] = body.description
        db_field_name = self._derive_db_field_name(body, body.name or field["name"], set())
        if db_field_name:
            updates["db_field_name"] = db_field_name

        if updates:
            updates["version"] = field["version"] + 1
            updates["last_modified_time"] = datetime.now(UTC).replace(tzinfo=None)
            updates["last_modified_by"] = cls.get("user.id")
            field = await repository.update_field_row(field_id, updates) or field
            from ...realtime.broadcast import broadcast_field_update, build_set_property_ops

            ops = build_set_property_ops(old_vo, _field_vo(field))
            await broadcast_field_update(table_id, field_id, ops, field["version"])
        return _field_vo(field)

    async def delete_field(self, table_id: str, field_id: str) -> None:
        await self._load_table(table_id)
        field = await repository.get_field_row(table_id, field_id)
        if field is None:
            raise _field_not_found_plain()
        if field.get("is_primary"):
            raise ApiError(
                "Cannot delete primary field",
                HttpErrorCode.RESTRICTED_RESOURCE,
                {"domainCode": "forbidden.table.delete_primary_field", "domainTags": ["forbidden"]},
            )
        await repository.soft_delete_field_row(
            field_id, datetime.now(UTC).replace(tzinfo=None), field["version"] + 1
        )
        from ...realtime.broadcast import broadcast_action_trigger, broadcast_field_delete

        await broadcast_field_delete(table_id, field_id, field["version"])
        await broadcast_action_trigger(
            table_id,
            [{"actionKey": "deleteField", "payload": {"tableId": table_id, "fieldId": field_id}}],
        )
        from ..undo_redo.stack import capture_operation

        await capture_operation(
            table_id,
            {
                "name": "deleteFields",
                "params": {"tableId": table_id},
                "result": {"fields": [_field_vo(field)]},
            },
        )

    async def delete_fields(self, table_id: str, field_ids: list[str]) -> None:
        await self._load_table(table_id)
        for field_id in field_ids:
            await self.delete_field(table_id, field_id)

    async def duplicate_field(
        self, table_id: str, field_id: str, body: DuplicateFieldBody
    ) -> dict[str, Any]:
        table = await self._load_table(table_id)
        source = await self._load_field(table_id, field_id)
        user_id = cls.get("user.id")
        now = datetime.now(UTC).replace(tzinfo=None)

        siblings = await table_repository.list_field_rows(table_id)
        name = _dedup_name(body.name, {f["name"] for f in siblings})
        db_field_name = _dedup_db_name(
            _DERIVED_DB_NAME_RE.sub("_", name), {f["db_field_name"] for f in siblings}
        )
        new_id_ = new_id(IdPrefix.FIELD)
        row = {
            "id": new_id_,
            "name": name,
            "description": source["description"],
            "type": source["type"],
            "db_field_name": db_field_name,
            "db_field_type": source["db_field_type"],
            "cell_value_type": source["cell_value_type"],
            "is_multiple_cell_value": source["is_multiple_cell_value"],
            "not_null": source["not_null"],
            "unique": source["unique"],
            "options": source["options"],
            "table_id": table_id,
            "order": await table_repository.max_field_order(table_id) + 1,
            "version": 1,
            "created_by": user_id,
            "last_modified_time": now,
            "last_modified_by": user_id,
        }
        await table_repository.execute_data_ddl(
            [ddl.add_field_column_sql(table["base_id"], table_id, db_field_name, source["type"])]
        )
        await table_repository.insert_field_rows([row])
        from ...realtime.broadcast import broadcast_field_create

        await broadcast_field_create(table_id, _field_vo(row))
        return _field_vo(row)

    async def delete_references(self, table_id: str, field_ids: list[str]) -> dict[str, Any]:
        await self._load_table(table_id)
        empty = {
            "workflowNodes": [],
            "authorityMatrixRoles": [],
            "views": [],
            "dependentFields": [],
        }
        return {field_id: dict(empty) for field_id in field_ids}

    async def plan_create(self, table_id: str, body: FieldCreateBody) -> dict[str, Any]:
        await self._load_table(table_id)
        return {"estimateTime": 0, "updateCellCount": 0}

    def _delete_plan_graph(
        self, table_id: str, table_name: str, field: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "nodes": [
                {
                    "id": field["id"],
                    "label": field["name"],
                    "comboId": table_id,
                    "fieldType": field["type"],
                    "isSelected": True,
                }
            ],
            "edges": [],
            "combos": [{"id": table_id, "label": table_name}],
        }

    async def plan_delete(self, table_id: str, field_id: str) -> dict[str, Any]:
        table = await self._load_table(table_id)
        field = await repository.get_field_row(table_id, field_id)
        if field is None:
            raise ApiError(
                f"Field {field_id} not found in table {table_id}",
                HttpErrorCode.NOT_FOUND,
                {
                    "localization": {
                        "i18nKey": "httpErrors.field.notFoundInTable",
                        "context": {"tableId": table_id, "fieldId": field_id},
                    }
                },
            )
        count = await repository.count_data_rows(table["base_id"], table_id)
        return {
            "graph": self._delete_plan_graph(table_id, table["name"], field),
            "updateCellCount": count,
            "estimateTime": count // 3,
        }

    async def plan_convert(
        self, table_id: str, field_id: str, body: FieldConvertBody
    ) -> dict[str, Any]:
        table = await self._load_table(table_id)
        field = await repository.get_field_row(table_id, field_id)
        if field is None:
            raise _field_not_found(field_id)
        count = await repository.count_non_null(table["base_id"], table_id, field["db_field_name"])
        return {
            "updateCellCount": count,
            "estimateTime": count // 3,
            "linkFieldCount": 0,
        }

    async def convert_field(
        self, table_id: str, field_id: str, body: FieldConvertBody
    ) -> dict[str, Any]:
        table = await self._load_table(table_id)
        field = await repository.get_field_row(table_id, field_id)
        if field is None:
            raise _field_not_found(field_id)
        if body.dbFieldName is not None and not _DB_FIELD_NAME_RE.match(body.dbFieldName):
            raise _invalid_db_field_name()

        old_vo = _field_vo(field)
        if body.isLookup:
            field = await self._convert_to_lookup(table, field, body)
            return await self._finish_convert(table_id, field_id, old_vo, field)
        if body.type == "rollup":
            field = await self._convert_to_rollup(table, field, body)
            return await self._finish_convert(table_id, field_id, old_vo, field)
        if body.type == "formula":
            field = await self._convert_to_formula(table, field, body)
            return await self._finish_convert(table_id, field_id, old_vo, field)
        if body.type == "link":
            field = await self._convert_to_link(table, field, body)
            return await self._finish_convert(table_id, field_id, old_vo, field)
        if field["type"] == "link" and body.type in CONVERT_SUPPORTED_TYPES:
            field = await self._convert_link_to_scalar(table, field, body)
            return await self._finish_convert(table_id, field_id, old_vo, field)
        if (field.get("is_computed") or field.get("is_lookup")) and (
            body.type in CONVERT_SUPPORTED_TYPES
        ):
            field = await self._convert_from_computed(table, field, body)
            return await self._finish_convert(table_id, field_id, old_vo, field)
        if not _scalar_convertible(field) or body.type not in CONVERT_SUPPORTED_TYPES:
            raise _convert_not_supported(field["type"], body.type)

        base_id = table["base_id"]
        old_db_field_name = field["db_field_name"]
        new_db_field_name = body.dbFieldName or old_db_field_name
        new_options = _normalize_options(body.type, body.options)
        updates: dict[str, Any] = {
            "type": body.type,
            "name": body.name or field["name"],
            "db_field_name": new_db_field_name,
            "cell_value_type": CELL_VALUE_TYPES[body.type],
            "db_field_type": DB_FIELD_TYPES[body.type],
            "is_multiple_cell_value": body.type in ("multipleSelect", "attachment", "user"),
            "options": json.dumps(new_options, separators=(",", ":")),
            "version": field["version"] + 1,
            "last_modified_time": datetime.now(UTC).replace(tzinfo=None),
            "last_modified_by": cls.get("user.id"),
        }

        if body.type == field["type"]:
            # same-type call: metadata/options update only. Choice rename/delete
            # remapping is field-converting's modifyOptions path, outside this
            # type-conversion slice.
            field = await repository.update_field_row(field_id, updates) or field
            if new_db_field_name != old_db_field_name:
                await repository.rename_field_column(
                    base_id, table_id, old_db_field_name, new_db_field_name
                )
        else:
            raw_cells = await repository.list_cell_values(
                base_id, table_id, old_db_field_name
            )
            new_field = await repository.update_field_row(field_id, updates) or field
            cell_values = await self._convert_cell_values(
                table_id, field, new_field, body.type, new_options, raw_cells
            )
            await repository.replace_field_column(
                base_id,
                table_id,
                old_db_field_name,
                new_db_field_name,
                ddl.FIELD_DB_TYPES[body.type],
                cell_values,
            )
            field = await repository.get_field_row(table_id, field_id) or new_field

        return await self._finish_convert(table_id, field_id, old_vo, field)

    async def _finish_convert(
        self,
        table_id: str,
        field_id: str,
        old_vo: dict[str, Any],
        field: dict[str, Any],
    ) -> dict[str, Any]:
        from ...realtime.broadcast import (
            broadcast_action_trigger,
            broadcast_field_update,
            build_set_property_ops,
        )

        ops = build_set_property_ops(old_vo, _field_vo(field))
        await broadcast_field_update(table_id, field_id, ops, field["version"])
        await broadcast_action_trigger(
            table_id,
            [{"actionKey": "setField", "payload": {"tableId": table_id, "fieldId": field_id}}],
        )
        return _field_vo(field)

    async def _convert_to_formula(
        self, table: dict[str, Any], field: dict[str, Any], body: FieldConvertBody
    ) -> dict[str, Any]:
        table_id = table["id"]
        base_id = table["base_id"]
        old_db = field["db_field_name"]
        new_db = body.dbFieldName or old_db
        options_ro = dict(body.options or {})
        expression = options_ro.get("expression")
        try:
            tree = parse_formula(expression or "")
        except FormulaError as exc:
            raise _formula_parse_error(expression, exc) from exc
        siblings = await table_repository.list_field_rows(table_id)
        field_by_id = {f["id"]: f for f in siblings}
        ref_ids = reference_field_ids(tree)
        missing = [rid for rid in ref_ids if rid not in field_by_id]
        if missing:
            raise _formula_reference_not_found(missing)
        try:
            cvt, is_multiple = parsed_value_type(
                tree, {rid: field_by_id[rid] for rid in ref_ids}
            )
        except FormulaError as exc:
            raise _formula_type_error(expression, exc) from exc
        options = dict(options_ro)
        fmt = options_ro.get("formatting", _default_formula_formatting(cvt))
        options["formatting"] = fmt
        if fmt is None:
            options.pop("formatting", None)
        options["timeZone"] = options_ro.get("timeZone", _FORMULA_TIMEZONE)
        db_type = _derive_db_field_type("formula", cvt, is_multiple)
        ids = [r for r, _ in await repository.list_cell_values(base_id, table_id, old_db)]
        updates = {
            "type": "formula", "name": body.name or field["name"],
            "db_field_name": new_db, "cell_value_type": cvt,
            "db_field_type": db_type, "is_computed": True, "is_lookup": None,
            "is_multiple_cell_value": is_multiple or None,
            "lookup_linked_field_id": None, "lookup_options": None,
            "options": json.dumps(options, separators=(",", ":")),
            "version": field["version"] + 1,
            "last_modified_time": datetime.now(UTC).replace(tzinfo=None),
            "last_modified_by": cls.get("user.id"),
        }
        new_field = await repository.update_field_row(field["id"], updates) or field
        await repository.replace_field_column(
            base_id, table_id, old_db, new_db,
            ddl.DB_FIELD_TYPE_TO_COLUMN[db_type], [],
        )
        from ..record.service import RecordService

        fields_now = await table_repository.list_field_rows(table_id)
        await RecordService()._materialize_computed(table, fields_now, ids)
        return await repository.get_field_row(table_id, field["id"]) or new_field

    async def _convert_to_lookup(
        self, table: dict[str, Any], field: dict[str, Any], body: FieldConvertBody
    ) -> dict[str, Any]:
        table_id = table["id"]
        base_id = table["base_id"]
        old_db = field["db_field_name"]
        new_db = body.dbFieldName or old_db
        lookup_ro = dict(body.lookupOptions or {})
        link_field, link_options, source_field = await self._resolve_lookup_source(
            table_id, lookup_ro
        )
        field_type = source_field["type"]
        is_multiple = bool(link_field.get("is_multiple_cell_value")) or bool(
            source_field.get("is_multiple_cell_value")
        )
        cvt = source_field["cell_value_type"]
        source_options = json.loads(source_field["options"] or "{}")
        options = _lookup_formatting_show_as(body.options, source_options, cvt)
        db_type = _derive_db_field_type(field_type, cvt, is_multiple)
        lookup_options = self._enriched_lookup_options(lookup_ro, link_options)
        ids = [r for r, _ in await repository.list_cell_values(base_id, table_id, old_db)]
        updates = {
            "type": field_type, "name": body.name or field["name"],
            "db_field_name": new_db, "cell_value_type": cvt, "db_field_type": db_type,
            "is_multiple_cell_value": is_multiple or None,
            "is_computed": True, "is_lookup": True,
            "lookup_linked_field_id": lookup_ro["linkFieldId"],
            "lookup_options": json.dumps(lookup_options, separators=(",", ":")),
            "options": json.dumps(options, separators=(",", ":")),
            "version": field["version"] + 1,
            "last_modified_time": datetime.now(UTC).replace(tzinfo=None),
            "last_modified_by": cls.get("user.id"),
        }
        new_field = await repository.update_field_row(field["id"], updates) or field
        await repository.replace_field_column(
            base_id, table_id, old_db, new_db,
            ddl.DB_FIELD_TYPE_TO_COLUMN[db_type], [],
        )
        from ..record.service import RecordService

        fields_now = await table_repository.list_field_rows(table_id)
        await RecordService()._materialize_computed(table, fields_now, ids)
        return await repository.get_field_row(table_id, field["id"]) or new_field

    async def _convert_to_rollup(
        self, table: dict[str, Any], field: dict[str, Any], body: FieldConvertBody
    ) -> dict[str, Any]:
        table_id = table["id"]
        base_id = table["base_id"]
        old_db = field["db_field_name"]
        new_db = body.dbFieldName or old_db
        options_ro = dict(body.options or {})
        expression = options_ro.get("expression")
        if not expression:
            raise ApiError(
                "rollup field options is required",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "editor.error.optionsRequired"}},
            )
        lookup_ro = dict(body.lookupOptions or {})
        link_field, link_options, source_field = await self._resolve_lookup_source(
            table_id, lookup_ro
        )
        source_multiple = bool(source_field.get("is_multiple_cell_value")) or bool(
            link_field.get("is_multiple_cell_value")
        )
        cvt, is_multiple = _rollup_return_type(
            expression, source_field["cell_value_type"], source_multiple
        )
        options: dict[str, Any] = {"expression": expression}
        if "timeZone" in options_ro:
            options["timeZone"] = options_ro["timeZone"]
        fmt = options_ro.get("formatting", _DEFAULT_FORMATTING.get(cvt))
        if fmt is not None:
            options["formatting"] = fmt
        if options_ro.get("showAs") is not None:
            options["showAs"] = options_ro["showAs"]
        db_type = _derive_db_field_type("rollup", cvt, is_multiple)
        lookup_options = self._enriched_lookup_options(lookup_ro, link_options)
        ids = [r for r, _ in await repository.list_cell_values(base_id, table_id, old_db)]
        updates = {
            "type": "rollup", "name": body.name or field["name"],
            "db_field_name": new_db, "cell_value_type": cvt, "db_field_type": db_type,
            "is_multiple_cell_value": is_multiple or None,
            "is_computed": True, "is_lookup": None,
            "lookup_linked_field_id": lookup_ro["linkFieldId"],
            "lookup_options": json.dumps(lookup_options, separators=(",", ":")),
            "options": json.dumps(options, separators=(",", ":")),
            "version": field["version"] + 1,
            "last_modified_time": datetime.now(UTC).replace(tzinfo=None),
            "last_modified_by": cls.get("user.id"),
        }
        new_field = await repository.update_field_row(field["id"], updates) or field
        await repository.replace_field_column(
            base_id, table_id, old_db, new_db,
            ddl.DB_FIELD_TYPE_TO_COLUMN[db_type], [],
        )
        from ..record.service import RecordService

        fields_now = await table_repository.list_field_rows(table_id)
        await RecordService()._materialize_computed(table, fields_now, ids)
        return await repository.get_field_row(table_id, field["id"]) or new_field

    async def _convert_to_link(
        self, table: dict[str, Any], field: dict[str, Any], body: FieldConvertBody
    ) -> dict[str, Any]:
        table_id = table["id"]
        base_id = table["base_id"]
        old_db = field["db_field_name"]
        new_db = body.dbFieldName or old_db
        options_ro = dict(body.options or {})
        relationship = options_ro.get("relationship")
        if relationship not in link_field.RELATIONSHIPS:
            raise _link_relationship_invalid()
        foreign_table_id = options_ro.get("foreignTableId")
        if not isinstance(foreign_table_id, str) or not foreign_table_id:
            raise _foreign_table_id_required()
        foreign_table = await repository.get_table_meta_by_id(foreign_table_id)
        if foreign_table is None or foreign_table["deleted_time"] is not None:
            raise _foreign_tables_not_found([foreign_table_id])
        base_id_opt = options_ro.get("baseId")
        if base_id_opt is not None and base_id_opt == base_id:
            base_id_opt = None
        foreign_fields = await table_repository.list_field_rows(foreign_table_id)
        lookup_field_id = options_ro.get("lookupFieldId") or _pick_link_lookup_field(
            foreign_fields
        )
        self_fields = await table_repository.list_field_rows(table_id)
        self_primary_id = next((f["id"] for f in self_fields if f.get("is_primary")), None)
        symmetric_field_id = None if options_ro.get("isOneWay") else new_id(IdPrefix.FIELD)
        options = link_field.build_link_options(
            options_ro=options_ro, field_id=field["id"],
            symmetric_field_id=symmetric_field_id, lookup_field_id=lookup_field_id,
            self_db_table_name=table["db_table_name"],
            foreign_db_table_name=foreign_table["db_table_name"], base_id=base_id_opt,
        )
        is_multiple = link_field.is_multi_value_link(relationship)
        old_cells = await repository.list_cell_values(base_id, table_id, old_db)
        old_field = dict(field)
        now = datetime.now(UTC).replace(tzinfo=None)
        updates = {
            "type": "link", "name": body.name or field["name"], "db_field_name": new_db,
            "cell_value_type": "string", "db_field_type": "JSON",
            "is_multiple_cell_value": is_multiple or None,
            "is_computed": None, "is_lookup": None,
            "lookup_linked_field_id": None, "lookup_options": None,
            "options": json.dumps(options, separators=(",", ":")),
            "version": field["version"] + 1, "last_modified_time": now,
            "last_modified_by": cls.get("user.id"),
        }
        new_field = await repository.update_field_row(field["id"], updates) or field
        await repository.replace_field_column(base_id, table_id, old_db, new_db, "jsonb", [])
        await table_repository.execute_data_ddl(
            ddl.link_relation_ddl(
                options, table["db_table_name"], foreign_table["db_table_name"]
            )
        )
        if symmetric_field_id is not None:
            await self._create_symmetric_field(
                table, foreign_table, foreign_fields, options, field["id"],
                self_primary_id, cls.get("user.id"), now,
            )
        await self._populate_links_from_titles(
            table_id, foreign_fields, lookup_field_id, old_field, old_cells, is_multiple
        )
        return await repository.get_field_row(table_id, field["id"]) or new_field

    async def _populate_links_from_titles(
        self,
        table_id: str,
        foreign_fields: list[dict[str, Any]],
        lookup_field_id: str,
        old_field: dict[str, Any],
        old_cells: list[tuple[str, Any]],
        is_multiple: bool,
    ) -> None:
        from ...config import get_settings
        from ..record.cell_format import cell_value_to_string
        from ..record.schemas import RecordBulkPatchBody
        from ..record.service import RecordService

        if not foreign_fields or not old_cells:
            return
        rs = RecordService()
        foreign_table_id = foreign_fields[0]["table_id"]
        lookup_field = next((f for f in foreign_fields if f["id"] == lookup_field_id), None)
        data = await rs.list_records(
            foreign_table_id, field_key_type="id", projection=[lookup_field_id],
            take=get_settings().max_read_rows, skip=0,
        )
        title_to_id: dict[str, str] = {}
        for rec in data["records"]:
            val = rec["fields"].get(lookup_field_id)
            if val is None:
                continue
            key = cell_value_to_string(lookup_field, val) if lookup_field else str(val)
            title_to_id.setdefault(key, rec["id"])
        patch_records: list[dict[str, Any]] = []
        for record_id, raw in old_cells:
            value = None if raw is None else RecordService._from_db_value(old_field, raw)
            if value is None:
                continue
            fid = title_to_id.get(cell_value_to_string(old_field, value))
            if fid is None:
                continue
            cell = [{"id": fid}] if is_multiple else {"id": fid}
            patch_records.append({"id": record_id, "fields": {old_field["id"]: cell}})
        if not patch_records:
            return
        patch = RecordBulkPatchBody.zod_validate(
            {"fieldKeyType": "id", "records": patch_records}
        )
        await rs.update_records(table_id, [r["id"] for r in patch_records], patch)

    async def _convert_link_to_scalar(
        self, table: dict[str, Any], field: dict[str, Any], body: FieldConvertBody
    ) -> dict[str, Any]:
        from ..record.service import RecordService
        from ..selection.typecast import FieldTypecaster

        table_id = table["id"]
        base_id = table["base_id"]
        old_db = field["db_field_name"]
        new_db = body.dbFieldName or old_db
        target = body.type
        options = json.loads(field["options"] or "{}")
        old_cells = await repository.list_cell_values(base_id, table_id, old_db)
        titles: list[tuple[str, str]] = []
        for record_id, raw in old_cells:
            val = None if raw is None else RecordService._from_db_value(field, raw)
            title = _link_cell_title(val)
            if title is not None:
                titles.append((record_id, title))
        new_options = _normalize_options(target, body.options)
        strings = [t for _, t in titles]
        if target in ("singleLineText", "longText"):
            cast: list[Any] = [s or None for s in strings]
        else:
            caster = FieldTypecaster(
                {"id": field["id"], "type": target, "options": new_options}
            )
            cast = caster.cast(strings)
            if target == "rating":
                mx = int(new_options.get("max") or 10)
                cast = [None if v is None else min(_js_round(v), mx) for v in cast]
            await caster.flush_new_choices(table_id)
        now = datetime.now(UTC).replace(tzinfo=None)
        updates = {
            "type": target, "name": body.name or field["name"], "db_field_name": new_db,
            "cell_value_type": CELL_VALUE_TYPES[target],
            "db_field_type": DB_FIELD_TYPES[target],
            "is_multiple_cell_value": target in ("multipleSelect", "attachment", "user"),
            "is_computed": None, "is_lookup": None,
            "lookup_linked_field_id": None, "lookup_options": None,
            "options": json.dumps(new_options, separators=(",", ":")),
            "version": field["version"] + 1, "last_modified_time": now,
            "last_modified_by": cls.get("user.id"),
        }
        new_field = await repository.update_field_row(field["id"], updates) or field
        cell_values = [
            (rid, RecordService._to_db_value(new_field, v))
            for (rid, _), v in zip(titles, cast, strict=True)
            if v is not None
        ]
        await table_repository.execute_data_ddl(ddl.link_relation_teardown_ddl(options))
        await repository.replace_field_column(
            base_id, table_id, old_db, new_db, ddl.FIELD_DB_TYPES[target], cell_values
        )
        sym_id = options.get("symmetricFieldId")
        if sym_id and options.get("foreignTableId"):
            await self._delete_symmetric_field(options["foreignTableId"], sym_id)
        return await repository.get_field_row(table_id, field["id"]) or new_field

    async def _delete_symmetric_field(self, foreign_table_id: str, sym_id: str) -> None:
        sym = await repository.get_field_row(foreign_table_id, sym_id)
        if sym is None:
            return
        await repository.soft_delete_field_row(
            sym_id, datetime.now(UTC).replace(tzinfo=None), sym["version"] + 1
        )
        from ...realtime.broadcast import broadcast_field_delete

        await broadcast_field_delete(foreign_table_id, sym_id, sym["version"])

    async def _convert_from_computed(
        self, table: dict[str, Any], field: dict[str, Any], body: FieldConvertBody
    ) -> dict[str, Any]:
        table_id = table["id"]
        base_id = table["base_id"]
        old_db = field["db_field_name"]
        new_db = body.dbFieldName or old_db
        target = body.type
        new_options = _normalize_options(target, body.options)
        # lookup/rollup keep their materialized values (ref preserves the stored
        # column); formula is compute-on-read and drops to an empty column.
        is_formula = field["type"] == "formula" and not field.get("is_lookup")
        raw_cells: list[tuple[str, Any]] = []
        if not is_formula:
            fields_now = await table_repository.list_field_rows(table_id)
            ids = [r for r, _ in await repository.list_cell_values(base_id, table_id, old_db)]
            from ..record.service import RecordService

            await RecordService()._materialize_computed(table, fields_now, ids)
            raw_cells = await repository.list_cell_values(base_id, table_id, old_db)
        updates = {
            "type": target, "name": body.name or field["name"],
            "db_field_name": new_db, "cell_value_type": CELL_VALUE_TYPES[target],
            "db_field_type": DB_FIELD_TYPES[target],
            "is_multiple_cell_value": target in ("multipleSelect", "attachment", "user"),
            "is_computed": None, "is_lookup": None,
            "lookup_linked_field_id": None, "lookup_options": None,
            "options": json.dumps(new_options, separators=(",", ":")),
            "version": field["version"] + 1,
            "last_modified_time": datetime.now(UTC).replace(tzinfo=None),
            "last_modified_by": cls.get("user.id"),
        }
        new_field = await repository.update_field_row(field["id"], updates) or field
        cell_values = await self._convert_cell_values(
            table_id, field, new_field, target, new_options, raw_cells
        )
        await repository.replace_field_column(
            base_id, table_id, old_db, new_db, ddl.FIELD_DB_TYPES[target], cell_values,
        )
        return await repository.get_field_row(table_id, field["id"]) or new_field

    async def _convert_cell_values(
        self,
        table_id: str,
        old_field: dict[str, Any],
        new_field: dict[str, Any],
        target_type: str,
        new_options: dict[str, Any],
        raw_cells: list[tuple[str, Any]],
    ) -> list[tuple[str, Any]]:
        """Port field-converting's per-record convertCellValue: stringify each
        stored value through the old field, then coerce it into the new type via
        the shared typecast engine (which also auto-creates select choices)."""
        from ..record.cell_format import cell_value_to_string
        from ..record.service import RecordService
        from ..selection.typecast import FieldTypecaster

        if target_type not in _CELL_MIGRATION_TARGETS:
            return []

        ids: list[str] = []
        strings: list[str] = []
        for record_id, raw in raw_cells:
            if raw is None:
                continue
            value = RecordService._from_db_value(old_field, raw)
            if value is None:
                continue
            ids.append(record_id)
            strings.append(cell_value_to_string(old_field, value))

        if target_type == "longText":
            # longText keeps interior newlines (its coercion only trims); the
            # shared caster folds them like singleLineText, so honor it here.
            cast: list[Any] = [s.strip() or None for s in strings]
        else:
            caster = FieldTypecaster(
                {"id": old_field["id"], "type": target_type, "options": new_options}
            )
            cast = caster.cast(strings)
            if target_type == "rating":
                max_value = int(new_options.get("max") or 10)
                cast = [None if v is None else min(_js_round(v), max_value) for v in cast]
            await caster.flush_new_choices(table_id)

        return [
            (record_id, RecordService._to_db_value(new_field, value))
            for record_id, value in zip(ids, cast, strict=True)
            if value is not None
        ]
