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

# resources exposed by GET /:tableId/permission (ref tablePermissionVo).
_TABLE_PERMISSION_RESOURCES = {
    "table": [
        "table|create",
        "table|delete",
        "table|read",
        "table|update",
        "table|import",
        "table|export",
        "table|trash_read",
        "table|trash_update",
        "table|trash_reset",
        "table|archive_read",
        "table|archive_manage",
    ],
    "field": ["field|create", "field|delete", "field|read", "field|update"],
    "record": [
        "record|create",
        "record|delete",
        "record|read",
        "record|update",
        "record|comment",
        "record|copy",
        "record|archive",
    ],
    "view": ["view|create", "view|delete", "view|read", "view|update", "view|share"],
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


def _table_not_found(table_id: str, base_id: str) -> ApiError:
    return ApiError(
        f"Table {table_id} not found in base {base_id}",
        HttpErrorCode.NOT_FOUND,
        {"localization": {"i18nKey": "httpErrors.notFound"}},
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
        vo: dict[str, Any] = {
            "id": table_row["id"],
            "name": table_row["name"],
            "dbTableName": table_row["db_table_name"],
            "defaultViewId": default_view_id,
            # the v2 VO serializes integral orders as ints
            "order": int(order) if float(order).is_integer() else order,
            "lastModifiedTime": _iso(table_row["last_modified_time"] or table_row["created_time"]),
        }
        if table_row.get("description") is not None:
            vo["description"] = table_row["description"]
        if table_row.get("icon") is not None:
            vo["icon"] = table_row["icon"]
        return vo

    async def get_table(self, base_id: str, table_id: str) -> dict[str, Any]:
        table_row = await repository.get_table_meta_row(table_id, base_id, include_deleted=True)
        if table_row is None:
            raise _table_not_found(table_id, base_id)
        if table_row["deleted_time"] is not None:
            raise ApiError(
                "Table not found",
                HttpErrorCode.NOT_FOUND,
                {"domainCode": "table.not_found", "domainTags": ["not-found"]},
            )
        default_view_ids = await repository.get_default_view_ids([table_id])
        if table_id not in default_view_ids:
            raise ApiError(
                "defaultViewId not found",
                HttpErrorCode.NOT_FOUND,
                {"localization": {"i18nKey": "httpErrors.view.defaultViewNotFound"}},
            )
        return self._table_vo(table_row, default_view_ids[table_id])

    async def update_name(self, base_id: str, table_id: str, name: str) -> None:
        table = await repository.get_table_meta_row(table_id, base_id)
        if table is None:
            raise _table_not_found(table_id, base_id)
        await repository.update_table_meta_row(
            table_id,
            {
                "name": name,
                "version": table["version"] + 1,
                "last_modified_time": datetime.now(UTC).replace(tzinfo=None),
                "last_modified_by": cls.get("user.id"),
            },
        )

    async def update_icon(self, base_id: str, table_id: str, icon: str | None) -> None:
        table = await repository.get_table_meta_row(table_id, base_id)
        if table is None:
            raise _table_not_found(table_id, base_id)
        await repository.update_table_meta_row(
            table_id,
            {
                "icon": icon,
                "version": table["version"] + 1,
                "last_modified_time": datetime.now(UTC).replace(tzinfo=None),
                "last_modified_by": cls.get("user.id"),
            },
        )

    async def update_description(
        self, base_id: str, table_id: str, description: str | None
    ) -> None:
        table = await repository.get_table_meta_row(table_id, base_id)
        if table is None:
            raise _table_not_found(table_id, base_id)
        await repository.update_table_meta_row(
            table_id,
            {
                "description": description,
                "version": table["version"] + 1,
                "last_modified_time": datetime.now(UTC).replace(tzinfo=None),
                "last_modified_by": cls.get("user.id"),
            },
        )

    async def update_db_table_name(self, base_id: str, table_id: str, name: str) -> None:
        # v1 route wording/errors (no v2 counterpart).
        candidate = f"{base_id}.{ddl.convert_name_to_valid_character(name, 63)}"
        exist = await repository.find_table_by_db_table_name(candidate, base_id)
        if exist and exist["id"] != table_id:
            raise ApiError(
                f"dbTableName {name} already exists",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.table.dbTableNameAlreadyExists"}},
            )
        table = await repository.get_table_meta_row(table_id, base_id)
        if table is None:
            raise ApiError(
                f"table {table_id} not found",
                HttpErrorCode.NOT_FOUND,
                {"localization": {"i18nKey": "httpErrors.table.notFound"}},
            )
        old = table["db_table_name"]
        old_table_name = old.split(".", 1)[1] if "." in old else old
        new_table_name = candidate.split(".", 1)[1]
        await repository.execute_data_ddl(
            [ddl.rename_data_table_sql(base_id, old_table_name, new_table_name)]
        )
        await repository.update_table_meta_row(
            table_id,
            {
                "db_table_name": candidate,
                "version": table["version"] + 1,
                "last_modified_time": datetime.now(UTC).replace(tzinfo=None),
                "last_modified_by": cls.get("user.id"),
            },
        )

    async def update_order(
        self, base_id: str, table_id: str, anchor_id: str, position: str
    ) -> None:
        table = await repository.get_table_meta_row(table_id, base_id)
        if table is None:
            raise _table_not_found(table_id, base_id)
        anchor = await repository.get_table_meta_row(anchor_id, base_id)
        if anchor is None:
            raise ApiError(
                f"Anchor {anchor_id} not found",
                HttpErrorCode.NOT_FOUND,
                {"localization": {"i18nKey": "httpErrors.table.anchorNotFound"}},
            )
        new_order = await self._compute_order(base_id, anchor, position)
        await repository.update_table_meta_row(
            table_id,
            {
                "order": new_order,
                "version": table["version"] + 1,
                "last_modified_time": datetime.now(UTC).replace(tzinfo=None),
                "last_modified_by": cls.get("user.id"),
            },
        )

    async def _compute_order(
        self, base_id: str, anchor: dict[str, Any], position: str
    ) -> float:
        below = position == "after"
        neighbor = await repository.list_next_table_by_order(
            base_id, anchor["order"], below=position == "before"
        )
        if neighbor is None:
            return anchor["order"] + (1 if below else -1)
        order = (neighbor["order"] + anchor["order"]) / 2
        if abs(order - anchor["order"]) < 2 * 2.220446049250313e-16:
            # gap exhausted: re-shuffle the base to integral orders, recompute.
            await self._shuffle_orders(base_id)
            anchor = await repository.get_table_meta_row(anchor["id"], base_id)  # type: ignore[assignment]
            assert anchor is not None
            return await self._compute_order(base_id, anchor, position)
        return order

    async def _shuffle_orders(self, base_id: str) -> None:
        for index, table in enumerate(await repository.list_table_meta_rows(base_id), start=1):
            await repository.update_table_meta_row(table["id"], {"order": float(index)})

    async def delete_table(self, base_id: str, table_id: str) -> None:
        table = await repository.get_table_meta_row(table_id, base_id)
        if table is None:
            raise _table_not_found(table_id, base_id)
        await repository.soft_delete_table_row(
            table_id, datetime.now(UTC).replace(tzinfo=None), table["version"] + 1
        )

    async def permanent_delete_table(self, base_id: str, table_id: str) -> None:
        table = await repository.get_table_meta_row(table_id, base_id, include_deleted=True)
        if table is None:
            raise _table_not_found(table_id, base_id)
        db_table_name = table["db_table_name"]
        table_name = db_table_name.split(".", 1)[1] if "." in db_table_name else db_table_name
        await repository.execute_data_ddl([ddl.drop_data_table_sql(base_id, table_name)])
        await repository.delete_table_cascade_rows(table_id)

    async def duplicate_table(
        self, base_id: str, table_id: str, name: str, include_records: bool
    ) -> dict[str, Any]:
        source = await repository.get_table_meta_row(table_id, base_id)
        if source is None:
            raise _table_not_found(table_id, base_id)
        user_id = cls.get("user.id")
        now = datetime.now(UTC).replace(tzinfo=None)

        source_fields = await repository.list_field_rows(table_id)
        source_views = await repository.list_view_rows(table_id)

        rows = await repository.list_table_names_and_orders(base_id)
        new_table_id = new_id(IdPrefix.TABLE)
        new_db_table_name = f"{base_id}.{new_table_id}"
        await repository.insert_table_meta(
            {
                "id": new_table_id,
                "base_id": base_id,
                "name": name,
                "db_table_name": new_db_table_name,
                "version": 1,
                "order": max((float(r["order"]) for r in rows), default=0.0) + 1,
                "created_by": user_id,
                "last_modified_by": user_id,
                "last_modified_time": now,
            }
        )

        field_map = {f["id"]: new_id(IdPrefix.FIELD) for f in source_fields}
        await repository.execute_data_ddl(ddl.create_data_table_sql(base_id, new_table_id))
        for field in source_fields:
            await repository.execute_data_ddl(
                [
                    ddl.add_field_column_sql(
                        base_id, new_table_id, field["db_field_name"], field["type"]
                    )
                ]
            )
        await repository.insert_field_rows(
            [
                {
                    "id": field_map[f["id"]],
                    "name": f["name"],
                    "type": f["type"],
                    "db_field_name": f["db_field_name"],
                    "db_field_type": f["db_field_type"],
                    "cell_value_type": f["cell_value_type"],
                    "is_multiple_cell_value": f["is_multiple_cell_value"],
                    "is_primary": f["is_primary"],
                    "not_null": f["not_null"],
                    "unique": f["unique"],
                    "is_computed": f["is_computed"],
                    "options": f["options"],
                    "table_id": new_table_id,
                    "order": f["order"],
                    "version": 1,
                    "created_by": user_id,
                    "last_modified_time": now,
                    "last_modified_by": user_id,
                }
                for f in source_fields
            ]
        )

        view_map = {v["id"]: new_id(IdPrefix.VIEW) for v in source_views}
        new_views = []
        for source_view in source_views:
            column_meta = {
                field_map.get(key, key): value
                for key, value in json.loads(source_view["column_meta"] or "{}").items()
            }
            new_views.append(
                await repository.insert_view_row(
                    {
                        "id": view_map[source_view["id"]],
                        "name": source_view["name"],
                        "table_id": new_table_id,
                        "type": source_view["type"],
                        "order": source_view["order"],
                        "version": 1,
                        "column_meta": json.dumps(column_meta, separators=(",", ":")),
                        "created_by": user_id,
                        "last_modified_time": now,
                        "last_modified_by": user_id,
                    }
                )
            )

        if include_records:
            columns = [
                "__id",
                "__created_time",
                "__last_modified_time",
                "__created_by",
                "__last_modified_by",
                "__version",
            ] + [f["db_field_name"] for f in source_fields]
            await repository.copy_data_rows(base_id, new_table_id, table_id, columns)

        prepared = [
            {
                "id": field_map[f["id"]],
                "name": f["name"],
                "type": f["type"],
                "dbFieldName": f["db_field_name"],
                "isPrimary": f["is_primary"],
                "notNull": f["not_null"],
                "unique": f["unique"] or False,
                "options": json.loads(f["options"] or "{}"),
            }
            for f in source_fields
        ]
        return {
            "id": new_table_id,
            "name": name,
            "dbTableName": new_db_table_name,
            "defaultViewId": new_views[0]["id"] if new_views else None,
            "fields": [self._field_vo(f) for f in prepared],
            "views": [self._view_vo(v) for v in new_views],
            "fieldMap": field_map,
            "viewMap": view_map,
        }

    async def get_permission(self) -> dict[str, dict[str, bool]]:
        granted = cls.get("permissions") or []
        result: dict[str, dict[str, bool]] = {res: {} for res in _TABLE_PERMISSION_RESOURCES}
        for action in granted:
            resource, _, op = action.partition("|")
            if op and resource in result:
                result[resource][action] = True
        for resource, actions in _TABLE_PERMISSION_RESOURCES.items():
            for action in actions:
                result[resource][action] = result[resource].get(action, False)
        return result

    async def list_tables(self, base_id: str) -> list[dict[str, Any]]:
        rows = await repository.list_table_meta_rows(base_id)
        default_view_ids = await repository.get_default_view_ids([r["id"] for r in rows])
        return [self._table_vo(r, default_view_ids.get(r["id"])) for r in rows]
