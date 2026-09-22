"""field domain service — ports features/field (field-open-api.service.ts v2 flows).

Only the REST-visible surface is ported: field meta CRUD against the physical
data table (add column on create). convert + plan routes land in a later
slice; link/lookup dependent routes stay empty until link fields exist.
"""

import json
import re
from datetime import UTC, datetime
from typing import Any

from ...core import cls
from ...core.errors import ApiError, HttpErrorCode
from ...core.ids import IdPrefix, new_id
from ...db import provider as ddl
from ..table import repository as table_repository
from ..table.service import CELL_VALUE_TYPES, DB_FIELD_TYPES
from . import repository
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


def _normalize_options(field_type: str, options: dict[str, Any] | None) -> dict[str, Any]:
    options = dict(options) if options else {}
    if field_type == "number":
        if "precision" in options:
            options["formatting"] = {
                "type": "decimal",
                "precision": options.pop("precision"),
            }
        options.setdefault("formatting", {"type": "decimal", "precision": 2})
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
        table = await self._load_table(table_id)
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

    async def get_field(self, table_id: str, field_id: str) -> dict[str, Any]:
        await self._load_table(table_id)
        return _field_vo(await self._load_field(table_id, field_id))

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

    async def update_field(
        self, table_id: str, field_id: str, body: FieldPatchBody
    ) -> dict[str, Any]:
        await self._load_table(table_id)
        field = await self._load_field(table_id, field_id)
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

        db_field_name = body.dbFieldName or field["db_field_name"]
        if body.dbFieldName is not None and not _DB_FIELD_NAME_RE.match(body.dbFieldName):
            raise _invalid_db_field_name()
        updates: dict[str, Any] = {
            "type": body.type,
            "name": body.name or field["name"],
            "db_field_name": db_field_name,
            "cell_value_type": CELL_VALUE_TYPES[body.type],
            "db_field_type": DB_FIELD_TYPES[body.type],
            "is_multiple_cell_value": body.type in ("multipleSelect", "attachment", "user"),
            "options": json.dumps(
                _normalize_options(body.type, body.options), separators=(",", ":")
            ),
            "version": field["version"] + 1,
            "last_modified_time": datetime.now(UTC).replace(tzinfo=None),
            "last_modified_by": cls.get("user.id"),
        }
        await repository.alter_field_column_type(
            table["base_id"], table_id, db_field_name, ddl.FIELD_DB_TYPES[body.type]
        )
        field = await repository.update_field_row(field_id, updates) or field
        return _field_vo(field)
