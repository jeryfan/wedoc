"""record domain service — ports features/record (record-open-api.service.ts).

Slice A: CRUD + list queries (viewId/filter/sort/projection/take/skip/cursor).
history/form-submit/TQL search land in later slices; attachments need the
storage stack; collaborators and socket endpoints are M3 realtime.
"""

import json
from datetime import UTC, datetime
from typing import Any

from ...core import cls
from ...core.errors import ApiError, HttpErrorCode
from ...core.ids import IdPrefix, new_id
from ..field.repository import get_table_meta_by_id
from ..table import repository as table_repository
from ..view.repository import get_view_row
from . import repository
from .schemas import RecordBulkPatchBody, RecordCreateBody, RecordPatchBody

DEFAULT_TAKE = 1000
JSONB_FIELD_TYPES = {"user", "attachment"}
ARRAY_FIELD_TYPES = {"multipleSelect"}

FILTER_OPERATORS = {
    "is": "= :{p}",
    "isNot": "IS DISTINCT FROM :{p}",
    "contains": "LIKE :{p}",
    "doesNotContain": "NOT LIKE :{p}",
    "gt": "> :{p}",
    "gte": ">= :{p}",
    "lt": "< :{p}",
    "lte": "<= :{p}",
}


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _record_not_found(localization: bool = False) -> ApiError:
    if localization:
        return ApiError(
            "Record not found",
            HttpErrorCode.NOT_FOUND,
            {"localization": {"i18nKey": "httpErrors.record.notFound"}},
        )
    return ApiError(
        "Record not found",
        HttpErrorCode.NOT_FOUND,
        {"domainCode": "record.not_found", "domainTags": ["not-found"]},
    )


class RecordService:
    async def _load_context(self, table_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        table = await get_table_meta_by_id(table_id)
        if table is None or table["deleted_time"] is not None:
            raise ApiError(
                "Table not found",
                HttpErrorCode.NOT_FOUND,
                {"domainCode": "table.not_found", "domainTags": ["not-found"]},
            )
        fields = await table_repository.list_field_rows(table_id)
        return table, fields

    def _field_by_key(
        self, fields: list[dict[str, Any]], key: str, key_type: str
    ) -> dict[str, Any]:
        column = "name" if key_type == "name" else "id"
        for field in fields:
            if field[column] == key:
                return field
        # id lookup misses fall back to a name match (ref resolves either key);
        # a total miss is reported in the name flavor, matching ref.
        if column == "id":
            for field in fields:
                if field["name"] == key:
                    return field
            column = "name"
        available = [f[column] for f in fields]
        raise ApiError(
            f'Field "{key}" does not exist in this table',
            HttpErrorCode.NOT_FOUND,
            {
                "domainCode": "field.key_not_found",
                "domainTags": ["not-found"],
                "details": {
                    "fieldKeyType": key_type,
                    "fieldKey": key,
                    "availableFieldKeys": available,
                },
            },
        )

    @staticmethod
    def _to_db_value(field: dict[str, Any], value: Any) -> Any:
        if value is None:
            return None
        if field["type"] in JSONB_FIELD_TYPES:
            return json.dumps(value, separators=(",", ":"))
        if field["type"] in ARRAY_FIELD_TYPES:
            return list(value) if isinstance(value, (list, tuple)) else [value]
        return value

    @staticmethod
    def _from_db_value(field: dict[str, Any], value: Any) -> Any:
        if value is None:
            return None
        if field["type"] in JSONB_FIELD_TYPES and isinstance(value, str):
            return json.loads(value)
        if field["cell_value_type"] == "dateTime" and isinstance(value, datetime):
            return _iso(value)
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return value

    def _full_vo(
        self, row: dict[str, Any], fields: list[dict[str, Any]], key_type: str = "id"
    ) -> dict[str, Any]:
        key_of = "name" if key_type == "name" else "id"
        values: dict[str, Any] = {}
        name_value: Any = None
        for field in fields:
            value = self._from_db_value(field, row.get(field["db_field_name"]))
            if value is not None:
                values[field[key_of]] = value
            if field.get("is_primary"):
                name_value = value
        vo: dict[str, Any] = {"id": row["__id"], "fields": values}
        vo["name"] = name_value if name_value is not None else ""
        vo["autoNumber"] = row["__auto_number"]
        vo["createdTime"] = _iso(row["__created_time"])
        last_modified = row.get("__last_modified_time") or row["__created_time"]
        vo["lastModifiedTime"] = _iso(last_modified)
        vo["createdBy"] = row["__created_by"]
        vo["lastModifiedBy"] = row.get("__last_modified_by") or row["__created_by"]
        return vo

    def _echo_vo(
        self,
        record_id: str,
        row: dict[str, Any],
        fields: list[dict[str, Any]],
        key_type: str = "id",
    ) -> dict[str, Any]:
        key_of = "id" if key_type == "id" else "name"
        values: dict[str, Any] = {}
        for field in fields:
            value = self._from_db_value(field, row.get(field["db_field_name"]))
            if value is not None:
                values[field[key_of]] = value
        return {"id": record_id, "fields": values}

    @staticmethod
    def _delete_echo(
        record_id: str, row: dict[str, Any], fields: list[dict[str, Any]]
    ) -> dict[str, Any]:
        # delete/duplicate echoes serialize every field, nulls included.
        values = {
            field["id"]: RecordService._from_db_value(field, row.get(field["db_field_name"]))
            for field in fields
        }
        return {"id": record_id, "fields": values}

    # ---- query compilation -------------------------------------------------

    def _compile_filter(
        self,
        fields: list[dict[str, Any]],
        filter_obj: dict[str, Any] | None,
        params: dict[str, Any],
        counter: list[int],
    ) -> str:
        if not filter_obj:
            return ""
        conjunction = filter_obj.get("conjunction") or "and"
        joiner = " AND " if conjunction == "and" else " OR "
        clauses = []
        for item in filter_obj.get("filterSet") or []:
            if "filterSet" in item:
                nested = self._compile_filter(fields, item, params, counter)
                if nested:
                    clauses.append(f"({nested})")
                continue
            operator = item.get("operator")
            template = FILTER_OPERATORS.get(operator)
            if template is None:
                continue
            field = self._field_by_key(fields, item.get("fieldId", ""), "id")
            value = item.get("value")
            if isinstance(value, str) and operator in ("contains", "doesNotContain"):
                value = f"%{value}%"
            param = f"f{counter[0]}"
            counter[0] += 1
            params[param] = self._to_db_value(field, value)
            clauses.append(f'"{field["db_field_name"]}" {template.format(p=param)}')
        return joiner.join(clauses)

    def _compile_sort(
        self, fields: list[dict[str, Any]], sort_items: list[dict[str, Any]] | None
    ) -> str:
        if not sort_items:
            return ""
        terms = []
        for item in sort_items:
            field = self._field_by_key(fields, item.get("fieldId", ""), "id")
            direction = "DESC" if item.get("order") == "desc" else "ASC"
            terms.append(f'"{field["db_field_name"]}" {direction}')
        return " ORDER BY " + ", ".join(terms)

    # ---- endpoints ---------------------------------------------------------

    async def get_record(
        self, table_id: str, record_id: str, field_key_type: str
    ) -> dict[str, Any]:
        table, fields = await self._load_context(table_id)
        row = await repository.fetch_row(
            table["base_id"], table_id, record_id, [f["db_field_name"] for f in fields]
        )
        if row is None:
            raise _record_not_found(localization=True)
        return self._full_vo(row, fields, field_key_type)

    async def list_records(
        self,
        table_id: str,
        field_key_type: str = "id",
        projection: list[str] | None = None,
        view_id: str | None = None,
        filter_param: dict[str, Any] | None = None,
        sort_param: list[dict[str, Any]] | None = None,
        take: int = DEFAULT_TAKE,
        skip: int = 0,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        table, fields = await self._load_context(table_id)

        view_filter: dict[str, Any] | None = None
        view_sort: list[dict[str, Any]] | None = None
        if view_id:
            view = await get_view_row(table_id, view_id)
            if view is not None:
                view_filter = json.loads(view["filter"]) if view.get("filter") else None
                view_sort = json.loads(view["sort"])["sortObjs"] if view.get("sort") else None

        selected = fields
        if projection:
            selected = [self._field_by_key(fields, p, "id") for p in projection]

        params: dict[str, Any] = {}
        counter = [0]
        clauses = []
        filter_obj = filter_param if filter_param is not None else view_filter
        compiled = self._compile_filter(fields, filter_obj, params, counter)
        if compiled:
            clauses.append(compiled)
        if cursor:
            clauses.append("__auto_number > :cursor")
            params["cursor"] = int(cursor)
        where_sql = " WHERE " + " AND ".join(clauses) if clauses else ""
        # ref silently ignores the top-level `sort` query param (probed with
        # asc/desc/sortObjs formats, all fall back to default order); only the
        # view's own sort applies via viewId.
        order_sql = self._compile_sort(fields, view_sort)

        rows = await repository.list_rows(
            table["base_id"],
            table_id,
            [f["db_field_name"] for f in fields],
            where_sql=where_sql,
            params=params,
            order_sql=order_sql or ' ORDER BY "__auto_number" ASC',
            limit=take + 1,
            offset=skip,
        )
        result: dict[str, Any] = {}
        if len(rows) > take:
            rows = rows[:take]
            result["extra"] = {"nextCursor": str(rows[-1]["__auto_number"])}
        result["records"] = [self._full_vo(r, selected, field_key_type) for r in rows]
        return result

    async def create_records(self, table_id: str, body: RecordCreateBody) -> dict[str, Any]:
        table, fields = await self._load_context(table_id)
        user_id = cls.get("user.id")
        records_vo = []
        for item in body.records:
            record_id = new_id(IdPrefix.RECORD)
            values: dict[str, Any] = {
                "__id": record_id,
                "__created_by": user_id,
                "__last_modified_by": user_id,
                "__version": 1,
            }
            submitted: dict[str, Any] = {}
            for key, value in item.fields.items():
                field = self._field_by_key(fields, key, body.fieldKeyType)
                values[field["db_field_name"]] = self._to_db_value(field, value)
                submitted[key] = value
            await repository.insert_row(table["base_id"], table_id, values)
            records_vo.append({"id": record_id, "fields": submitted})
        return {"records": records_vo}

    async def update_record(
        self, table_id: str, record_id: str, body: RecordPatchBody
    ) -> dict[str, Any]:
        table, fields = await self._load_context(table_id)
        columns = [f["db_field_name"] for f in fields]
        row = await repository.fetch_row(table["base_id"], table_id, record_id, columns)
        if row is None:
            raise _record_not_found()
        updates = {}
        for key, value in body.record.fields.items():
            field = self._field_by_key(fields, key, body.fieldKeyType)
            updates[field["db_field_name"]] = self._to_db_value(field, value)
        if updates:
            await repository.update_row(table["base_id"], table_id, record_id, updates)
            row = await repository.fetch_row(table["base_id"], table_id, record_id, columns) or row
        return self._echo_vo(record_id, row, fields, body.fieldKeyType)

    async def update_records(
        self, table_id: str, record_ids: list[str], body: RecordBulkPatchBody
    ) -> list[dict[str, Any]]:
        table, fields = await self._load_context(table_id)
        columns = [f["db_field_name"] for f in fields]
        results = []
        by_id = {r.id: r.fields for r in body.records}
        for record_id in record_ids:
            fields_ro = by_id.get(record_id)
            if fields_ro is None:
                continue
            row = await repository.fetch_row(table["base_id"], table_id, record_id, columns)
            if row is None:
                raise _record_not_found()
            updates = {}
            for key, value in fields_ro.items():
                field = self._field_by_key(fields, key, body.fieldKeyType)
                updates[field["db_field_name"]] = self._to_db_value(field, value)
            if updates:
                await repository.update_row(table["base_id"], table_id, record_id, updates)
                row = (
                    await repository.fetch_row(table["base_id"], table_id, record_id, columns)
                    or row
                )
            results.append(self._echo_vo(record_id, row, fields, body.fieldKeyType))
        return results

    async def delete_record(
        self, table_id: str, record_id: str
    ) -> dict[str, Any] | None:
        table, fields = await self._load_context(table_id)
        row = await repository.fetch_row(
            table["base_id"], table_id, record_id, [f["db_field_name"] for f in fields]
        )
        if row is None:
            return None
        await repository.delete_row(table["base_id"], table_id, record_id)
        return self._delete_echo(record_id, row, fields)

    async def delete_records(
        self, table_id: str, record_ids: list[str]
    ) -> dict[str, Any]:
        table, fields = await self._load_context(table_id)
        columns = [f["db_field_name"] for f in fields]
        echoes = []
        for rid in record_ids:
            row = await repository.fetch_row(table["base_id"], table_id, rid, columns)
            if row is None:
                continue
            await repository.delete_row(table["base_id"], table_id, rid)
            echoes.append(self._delete_echo(rid, row, fields))
        return {"records": echoes}

    async def duplicate_record(self, table_id: str, record_id: str) -> dict[str, Any]:
        table, fields = await self._load_context(table_id)
        row = await repository.fetch_row(
            table["base_id"], table_id, record_id, [f["db_field_name"] for f in fields]
        )
        if row is None:
            raise _record_not_found()
        new_record_id = new_id(IdPrefix.RECORD)
        values: dict[str, Any] = {
            "__id": new_record_id,
            "__created_by": cls.get("user.id"),
            "__last_modified_by": cls.get("user.id"),
            "__version": 1,
        }
        for field in fields:
            column = field["db_field_name"]
            if row.get(column) is not None:
                values[column] = row[column]
        await repository.insert_row(table["base_id"], table_id, values)
        return self._delete_echo(new_record_id, row, fields)

    async def get_status(self, table_id: str, record_id: str) -> dict[str, Any]:
        table, _fields = await self._load_context(table_id)
        row = await repository.fetch_row(table["base_id"], table_id, record_id, [])
        if row is None:
            raise _record_not_found(localization=True)
        return {"isDeleted": False, "isVisible": True}
