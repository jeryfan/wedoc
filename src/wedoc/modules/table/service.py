"""table domain service — ports features/table (table.service.ts +
open-api/table-open-api.service.ts create/get flows).

Only the REST-visible surface is ported: meta rows, physical data table DDL,
field/view/record provisioning for table creation. ShareDB rawOps, the
schemaOperation outbox and the undo-capture trigger are M3 realtime
infrastructure and are not emitted (see docs/api-parity-ledger.md).
Field/view meta persistence lives in this module until the field and view
modules absorb it.
"""

import json
import time
from datetime import UTC, datetime
from typing import Any

from ...core import cls
from ...core.errors import ApiError, HttpErrorCode
from ...core.ids import IdPrefix, new_id
from ...db import provider as ddl
from . import repository
from .schemas import PRIMARY_SUPPORTED_TYPES

DEFAULT_FIELDS: list[dict[str, Any]] = [
    {"name": "Name", "type": "singleLineText"},
    {"name": "Count", "type": "number"},
    {
        "name": "Status",
        "type": "singleSelect",
        "options": {
            "choices": [
                {"name": "light", "color": "grayBright"},
                {"name": "medium", "color": "yellowBright"},
                {"name": "heavy", "color": "tealBright"},
            ]
        },
    },
]
DEFAULT_VIEWS: list[dict[str, Any]] = [{"name": "Grid view", "type": "grid", "columnMeta": {}}]
DEFAULT_RECORDS: list[dict[str, Any]] = [{"fields": {}}, {"fields": {}}, {"fields": {}}]

CELL_VALUE_TYPES = {
    "singleLineText": "string",
    "longText": "string",
    "singleSelect": "string",
    "number": "number",
    "rating": "number",
    "autoNumber": "number",
    "checkbox": "boolean",
    "date": "dateTime",
    "createdTime": "dateTime",
    "lastModifiedTime": "dateTime",
    "createdBy": "string",
    "lastModifiedBy": "string",
    "user": "string",
    "multipleSelect": "string",
    "attachment": "string",
    "button": "string",
}

MULTIPLE_CELL_TYPES = {"multipleSelect", "attachment", "user"}

DB_FIELD_TYPES = {
    "singleLineText": "TEXT",
    "longText": "TEXT",
    "singleSelect": "TEXT",
    "multipleSelect": "TEXT",
    "number": "REAL",
    "rating": "REAL",
    "autoNumber": "REAL",
    "checkbox": "BOOLEAN",
    "date": "DATETIME",
    "createdTime": "DATETIME",
    "lastModifiedTime": "DATETIME",
    "createdBy": "TEXT",
    "lastModifiedBy": "TEXT",
    "user": "JSON",
    "attachment": "JSON",
    "button": "TEXT",
}

RESERVED_DB_FIELD_NAMES = {
    "__id",
    "__auto_number",
    "__created_time",
    "__last_modified_time",
    "__created_by",
    "__last_modified_by",
    "__version",
}


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _unsupported_field_type(field_type: str) -> ApiError:
    return ApiError(
        f"Unsupported field type {field_type}",
        HttpErrorCode.VALIDATION_ERROR,
        {
            "localization": {
                "i18nKey": "httpErrors.field.unsupportedFieldType",
                "context": {"type": field_type},
            }
        },
    )


def _normalize_options(field_type: str, options: dict[str, Any] | None) -> dict[str, Any]:
    options = dict(options) if options else {}
    if field_type == "number" and "formatting" not in options:
        options["formatting"] = {"type": "decimal", "precision": 2}
    if field_type in ("singleSelect", "multipleSelect"):
        choices = []
        for choice in options.get("choices", []):
            choices.append(
                {
                    "id": choice.get("id") or new_id(IdPrefix.CHOICE, 8),
                    "name": choice["name"],
                    "color": choice.get("color", "grayBright"),
                }
            )
        if choices or "choices" in options:
            options["choices"] = choices
    return options


class TableService:
    async def create_table(self, base_id: str, body: Any) -> dict[str, Any]:
        user_id = cls.get("user.id")
        now = datetime.now(UTC).replace(tzinfo=None)

        def _raw(item: Any) -> dict[str, Any]:
            return item if isinstance(item, dict) else item.model_dump(exclude_none=True)

        field_ros = (
            [_raw(f) for f in body.fields] if body.fields else [dict(f) for f in DEFAULT_FIELDS]
        )
        view_ros = [_raw(v) for v in body.views] if body.views else [dict(v) for v in DEFAULT_VIEWS]
        record_ros = (
            [_raw(r) for r in body.records] if body.records else [dict(r) for r in DEFAULT_RECORDS]
        )

        if not any(f.get("isPrimary") for f in field_ros):
            field_ros[0]["isPrimary"] = True
        first_type = field_ros[0]["type"]
        if first_type not in PRIMARY_SUPPORTED_TYPES:
            raise ApiError(
                f"Field type {first_type} is not supported as primary field",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.field.primaryFieldNotSupported"}},
            )

        rows = await repository.list_table_names_and_orders(base_id)
        # v2 keeps the requested name verbatim — no uniq-name dedup.
        table_name = body.name or "New table"
        order = max((float(r["order"]) for r in rows), default=0.0) + 1
        table_id = new_id(IdPrefix.TABLE)

        db_table_name = f"{base_id}.{table_id}"
        if body.dbTableName:
            candidate = f"{base_id}.{ddl.convert_name_to_valid_character(body.dbTableName, 63)}"
            if await repository.find_table_by_db_table_name(candidate, base_id):
                raise ApiError(
                    f"dbTableName {body.dbTableName} already exists",
                    HttpErrorCode.VALIDATION_ERROR,
                    {"localization": {"i18nKey": "httpErrors.table.dbTableNameAlreadyExists"}},
                )
            db_table_name = candidate

        table_row = await repository.insert_table_meta(
            {
                "id": table_id,
                "base_id": base_id,
                "name": table_name,
                # v2 silently drops description/icon on table create.
                "db_table_name": db_table_name,
                "version": 1,
                "order": order,
                "created_by": user_id,
                "last_modified_by": user_id,
                "last_modified_time": now,
            }
        )

        prepared = self._prepare_fields(field_ros)
        try:
            await repository.execute_data_ddl(ddl.create_data_table_sql(base_id, table_id))
            for field in prepared:
                await repository.execute_data_ddl(
                    [
                        ddl.add_field_column_sql(
                            base_id, table_id, field["dbFieldName"], field["type"]
                        )
                    ]
                )
        except Exception:
            await repository.execute_data_ddl([ddl.drop_data_table_sql(base_id, table_id)])
            await repository.update_table_meta_row(table_id, {"provision_state": "error"})
            raise

        await repository.insert_field_rows(
            [
                {
                    "id": field["id"],
                    "name": field["name"],
                    "type": field["type"],
                    "db_field_name": field["dbFieldName"],
                    "db_field_type": DB_FIELD_TYPES[field["type"]],
                    "cell_value_type": CELL_VALUE_TYPES[field["type"]],
                    "is_multiple_cell_value": field["type"] in MULTIPLE_CELL_TYPES or None,
                    "is_primary": field.get("isPrimary") or None,
                    "not_null": field.get("notNull") or None,
                    "unique": field.get("unique") or False,
                    "is_computed": None,
                    "options": json.dumps(field["options"], separators=(",", ":")),
                    "table_id": table_id,
                    "order": float(index + 1),
                    "version": 1,
                    "created_by": user_id,
                    "last_modified_time": now,
                    "last_modified_by": user_id,
                }
                for index, field in enumerate(prepared)
            ]
        )

        view_rows = []
        for index, view_ro in enumerate(view_ros):
            view_id = new_id(IdPrefix.VIEW)
            column_meta = view_ro.get("columnMeta") or {
                field["id"]: {"order": i} for i, field in enumerate(prepared)
            }
            view_rows.append(
                await repository.insert_view_row(
                    {
                        "id": view_id,
                        "name": view_ro.get("name") or f"Grid view {index + 1}",
                        "table_id": table_id,
                        "type": view_ro.get("type") or "grid",
                        "order": float(index),
                        "version": 1,
                        "column_meta": json.dumps(column_meta, separators=(",", ":")),
                        "created_by": user_id,
                        "last_modified_time": now,
                        "last_modified_by": user_id,
                    }
                )
            )

        records_vo = await self._create_records(
            base_id, table_id, prepared, record_ros, body.fieldKeyType or "name"
        )

        return {
            "id": table_id,
            "name": table_row["name"],
            "dbTableName": db_table_name,
            "defaultViewId": view_rows[0]["id"] if view_rows else None,
            "fields": [self._field_vo(f) for f in prepared],
            "views": [self._view_vo(v) for v in view_rows],
            "records": records_vo,
        }

    def _prepare_fields(self, field_ros: list[Any]) -> list[dict[str, Any]]:
        seen_names: set[str] = set()
        seen_db_names: set[str] = set(RESERVED_DB_FIELD_NAMES)
        prepared: list[dict[str, Any]] = []
        for field_ro in field_ros:
            raw = field_ro if isinstance(field_ro, dict) else field_ro.model_dump()
            name = raw["name"]
            if name in seen_names:
                raise ApiError(
                    f"Field name {name} already exists",
                    HttpErrorCode.VALIDATION_ERROR,
                    {"localization": {"i18nKey": "httpErrors.field.fieldNameAlreadyExists"}},
                )
            seen_names.add(name)
            field_type = raw["type"]
            if field_type not in CELL_VALUE_TYPES:
                raise _unsupported_field_type(field_type)
            db_field_name = raw.get("dbFieldName") or ddl.convert_name_to_valid_character(name, 40)
            if db_field_name in seen_db_names:
                db_field_name += str(int(time.time() * 1000))
            seen_db_names.add(db_field_name)
            prepared.append(
                {
                    "id": raw.get("id") or new_id(IdPrefix.FIELD),
                    "name": name,
                    "type": field_type,
                    "dbFieldName": db_field_name,
                    "isPrimary": raw.get("isPrimary") or None,
                    "notNull": raw.get("notNull") or None,
                    "unique": raw.get("unique") or False,
                    "options": _normalize_options(field_type, raw.get("options")),
                }
            )
        return prepared

    async def _create_records(
        self,
        base_id: str,
        table_id: str,
        fields: list[dict[str, Any]],
        record_ros: list[Any],
        field_key_type: str,
    ) -> list[dict[str, Any]]:
        user_id = cls.get("user.id")
        columns = ["__id", "__created_by", "__version"] + [f["dbFieldName"] for f in fields]
        value_rows: list[list[Any]] = []
        records_vo: list[dict[str, Any]] = []
        for record_ro in record_ros:
            raw = record_ro if isinstance(record_ro, dict) else record_ro.model_dump()
            record_fields = raw.get("fields") or {}
            record_id = new_id(IdPrefix.RECORD)
            values: list[Any] = [record_id, user_id, 1]
            vo_fields: dict[str, Any] = {}
            for field in fields:
                if field_key_type == "id":
                    value = record_fields.get(field["id"])
                else:
                    value = record_fields.get(field["name"])
                    if value is None:
                        value = record_fields.get(field["id"])
                values.append(value)
                vo_fields[field["name"]] = value
            value_rows.append(values)
            records_vo.append({"id": record_id, "fields": vo_fields})
        await repository.insert_data_rows(base_id, table_id, columns, value_rows)
        return records_vo

    @staticmethod
    def _field_vo(field: dict[str, Any]) -> dict[str, Any]:
        vo: dict[str, Any] = {
            "id": field["id"],
            "name": field["name"],
            "dbFieldName": field["dbFieldName"],
        }
        if field.get("isPrimary"):
            vo["isPrimary"] = True
        vo["unique"] = bool(field.get("unique"))
        vo["cellValueType"] = CELL_VALUE_TYPES[field["type"]]
        vo["dbFieldType"] = DB_FIELD_TYPES[field["type"]]
        vo["type"] = field["type"]
        vo["options"] = field["options"]
        return vo

    @staticmethod
    def _view_vo(view_row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": view_row["id"],
            "name": view_row["name"],
            "type": view_row["type"],
            "columnMeta": json.loads(view_row["column_meta"] or "{}"),
        }

    @staticmethod
    def _table_vo(table_row: dict[str, Any], default_view_id: str | None) -> dict[str, Any]:
        order = table_row["order"]
        return {
            "id": table_row["id"],
            "name": table_row["name"],
            "dbTableName": table_row["db_table_name"],
            "defaultViewId": default_view_id,
            # the v2 VO serializes integral orders as ints
            "order": int(order) if float(order).is_integer() else order,
            "lastModifiedTime": _iso(table_row["last_modified_time"] or table_row["created_time"]),
        }

    async def get_table(self, base_id: str, table_id: str) -> dict[str, Any]:
        table_row = await repository.get_table_meta_row(table_id, base_id)
        if table_row is None:
            raise ApiError(
                f"Table {table_id} not found in base {base_id}",
                HttpErrorCode.NOT_FOUND,
                {"localization": {"i18nKey": "httpErrors.notFound"}},
            )
        default_view_ids = await repository.get_default_view_ids([table_id])
        if table_id not in default_view_ids:
            raise ApiError(
                "defaultViewId not found",
                HttpErrorCode.NOT_FOUND,
                {"localization": {"i18nKey": "httpErrors.view.defaultViewNotFound"}},
            )
        return self._table_vo(table_row, default_view_ids[table_id])

    async def list_tables(self, base_id: str) -> list[dict[str, Any]]:
        rows = await repository.list_table_meta_rows(base_id)
        default_view_ids = await repository.get_default_view_ids([r["id"] for r in rows])
        return [self._table_vo(r, default_view_ids.get(r["id"])) for r in rows]
