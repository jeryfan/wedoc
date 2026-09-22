"""record domain service — ports features/record (record-open-api.service.ts).

Slice A: CRUD + list queries (viewId/filter/sort/projection/take/skip/cursor).
history/form-submit/TQL search land in later slices; attachments need the
storage stack; collaborators and socket endpoints are M3 realtime.
"""

import base64
import json
from datetime import UTC, datetime
from typing import Any

from ...core import cls
from ...core.errors import ApiError, HttpErrorCode
from ...core.ids import IdPrefix, new_id
from ...core.storage import get_public_full_storage_url
from ..field.repository import get_table_meta_by_id
from ..table import repository as table_repository
from ..view.repository import get_view_row
from . import repository
from .schemas import (
    RecordBulkPatchBody,
    RecordCreateBody,
    RecordPatchBody,
    RecordSubmitBody,
)
from .tql import TqlParseError, parse_tql

DEFAULT_TAKE = 1000
JSONB_FIELD_TYPES = {"user", "attachment"}
ARRAY_FIELD_TYPES = {"multipleSelect"}

# number fields created without explicit options carry these auto-filled
# display defaults in wedoc's field row; the reference leaves the row NULL and
# derives the same defaults when serializing, so history must treat them as
# "not set".
HISTORY_DEFAULT_OPTIONS = {"number": {"formatting": {"type": "decimal", "precision": 2}}}

FILTER_OPERATORS = {
    "is": "= :{p}",
    "isNot": "IS DISTINCT FROM :{p}",
    "contains": "LIKE :{p}",
    "doesNotContain": "NOT LIKE :{p}",
    "isGreater": "> :{p}",
    "isGreaterEqual": ">= :{p}",
    "isLess": "< :{p}",
    "isLessEqual": "<= :{p}",
}

# operator enum accepted by the reference filter schema; anything outside it
# fails request validation before reaching the query builder.
_FILTER_ENUM = set(FILTER_OPERATORS) | {"isEmpty", "isNotEmpty"}

# operators that the reference rejects at query-build time for scalar fields.
_UNSUPPORTED_FIELD_OPS = {"isAnyOf", "isNoneOf", "hasAnyOf", "hasAllOf"}


def _invalid_filter_operator() -> ApiError:
    return ApiError(
        "Invalid record condition operator for field",
        HttpErrorCode.VALIDATION_ERROR,
        {"domainCode": "validation.invalid", "domainTags": ["validation"]},
    )


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


def history_options(field_type: str, options_raw: str | None) -> Any:
    # ref stores NULL options on the field row and derives display defaults
    # in the VO; history rows therefore carry options: null for fields that
    # never set them explicitly (incl. auto-filled type defaults).
    if not options_raw:
        return None
    options = json.loads(options_raw)
    if not options:
        return None
    if options == HISTORY_DEFAULT_OPTIONS.get(field_type):
        return None
    return options


def build_history_row(
    *,
    table_id: str,
    record_id: str,
    field_id: str,
    name: str,
    field_type: str,
    options_raw: str | None,
    cell_value_type: str,
    before: Any,
    after: Any,
    user_id: str,
) -> dict[str, Any] | None:
    """One record_history row for a field value change; None when unchanged."""
    if before == after:
        return None
    meta = {
        "type": field_type,
        "name": name,
        "options": history_options(field_type, options_raw),
        "cellValueType": cell_value_type,
    }
    return {
        "id": new_id(IdPrefix.RECORD_HISTORY),
        "table_id": table_id,
        "record_id": record_id,
        "field_id": field_id,
        "before": json.dumps({"meta": meta, "data": before}, separators=(",", ":")),
        "after": json.dumps({"meta": meta, "data": after}, separators=(",", ":")),
        "created_by": user_id,
    }


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

    def _resolve_field(
        self, fields: list[dict[str, Any]], key: str
    ) -> dict[str, Any] | None:
        # filter/sort field refs accept id or name; unknown refs are dropped
        # silently by the reference query builder (match-all), never 404.
        for field in fields:
            if field["id"] == key or field["name"] == key:
                return field
        return None

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
            if operator in _UNSUPPORTED_FIELD_OPS:
                raise _invalid_filter_operator()
            if operator not in _FILTER_ENUM:
                continue
            field = self._resolve_field(fields, item.get("fieldId", ""))
            if field is None:
                continue
            column = f'"{field["db_field_name"]}"'
            value = item.get("value")
            if operator == "isEmpty" or (operator == "is" and value is None):
                if field["cell_value_type"] == "string":
                    clauses.append(f"({column} IS NULL OR {column} = '')")
                else:
                    clauses.append(f"{column} IS NULL")
                continue
            if operator == "isNotEmpty" or (operator == "isNot" and value is None):
                if field["cell_value_type"] == "string":
                    clauses.append(f"({column} IS NOT NULL AND {column} != '')")
                else:
                    clauses.append(f"{column} IS NOT NULL")
                continue
            template = FILTER_OPERATORS[operator]
            if isinstance(value, str) and operator in ("contains", "doesNotContain"):
                value = f"%{value}%"
            param = f"f{counter[0]}"
            counter[0] += 1
            params[param] = self._to_db_value(field, value)
            clauses.append(f"{column} {template.format(p=param)}")
        return joiner.join(clauses)

    def _compile_sort(
        self, fields: list[dict[str, Any]], sort_items: list[dict[str, Any]] | None
    ) -> str:
        if not sort_items:
            return ""
        terms = []
        for item in sort_items:
            field = self._resolve_field(fields, item.get("fieldId", ""))
            if field is None:
                continue
            direction = "DESC" if item.get("order") == "desc" else "ASC"
            terms.append(f'"{field["db_field_name"]}" {direction}')
        return " ORDER BY " + ", ".join(terms) if terms else ""

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
        order_by: list[dict[str, Any]] | None = None,
        tql: str | None = None,
        search: list[Any] | None = None,
        ignore_view_query: bool = False,
        take: int = DEFAULT_TAKE,
        skip: int = 0,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        table, fields = await self._load_context(table_id)

        # search arrives as the raw repeated-key values: [value, field, isExact].
        # A single value fails the tuple schema before any querying happens.
        if search is not None and len(search) < 2:
            raise ApiError(
                'Validation error: Invalid input: expected tuple, received string'
                ' at "search"',
                HttpErrorCode.VALIDATION_ERROR,
            )
        search_filter: dict[str, Any] | None = None
        if search is not None and len(search) == 3 and search[2] in ("true", True):
            # exact search compiles to a plain equality predicate on the field;
            # an unknown field silently drops the predicate (match-all).
            if self._resolve_field(fields, str(search[1])) is not None:
                search_filter = {
                    "conjunction": "and",
                    "filterSet": [
                        {"fieldId": search[1], "operator": "is", "value": search[0]}
                    ],
                }
        # the non-exact branch searches an index the reference build never
        # populates, so it matches every row: no predicate is added.

        view_filter: dict[str, Any] | None = None
        view_sort: list[dict[str, Any]] | None = None
        if view_id:
            view = await get_view_row(table_id, view_id)
            if view is None:
                raise ApiError(
                    f"View not found: {view_id}",
                    HttpErrorCode.NOT_FOUND,
                    {"domainCode": "view.not_found", "domainTags": ["not-found"]},
                )
            if not ignore_view_query:
                view_filter = json.loads(view["filter"]) if view.get("filter") else None
                view_sort = json.loads(view["sort"])["sortObjs"] if view.get("sort") else None

        # filterByTql overwrites the filter param (ref TqlPipe assigns it).
        query_filter = filter_param
        if tql:
            try:
                query_filter = parse_tql(tql)
            except TqlParseError as exc:
                raise ApiError(
                    f"TQL parse error, {exc}",
                    HttpErrorCode.VALIDATION_ERROR,
                ) from None

        filter_parts = []
        if view_filter:
            filter_parts.append(view_filter)
        if query_filter:
            filter_parts.append(query_filter)
        if search_filter:
            filter_parts.append(search_filter)
        if len(filter_parts) > 1:
            filter_obj: dict[str, Any] | None = {
                "conjunction": "and",
                "filterSet": filter_parts,
            }
        else:
            filter_obj = filter_parts[0] if filter_parts else None

        selected = fields
        if projection:
            selected = [self._field_by_key(fields, p, "id") for p in projection]

        params: dict[str, Any] = {}
        counter = [0]
        clauses = []
        compiled = self._compile_filter(fields, filter_obj, params, counter)
        if compiled:
            clauses.append(compiled)
        if cursor:
            clauses.append("__auto_number > :cursor")
            params["cursor"] = int(cursor)
        where_sql = " WHERE " + " AND ".join(clauses) if clauses else ""
        # the legacy `sort` param is ignored by ref; `orderBy` overrides the
        # view's sort, which in turn overrides the default auto-number order.
        order_sql = self._compile_sort(fields, order_by) or self._compile_sort(
            fields, view_sort
        )

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
        if search is not None and len(search) >= 2:
            # search never removes rows (exact search already filtered above);
            # it only annotates the page with the cells that matched.
            hit_field = self._resolve_field(fields, str(search[1]))
            hits: list[dict[str, Any]] = []
            if hit_field is not None:
                column = hit_field["db_field_name"]
                exact = len(search) == 3 and search[2] in ("true", True)
                needle = str(search[0])
                for row in rows:
                    raw = row.get(column)
                    if raw is None:
                        continue
                    matched = str(raw) == needle if exact else needle.lower() in str(raw).lower()
                    if matched:
                        hits.append({"fieldId": hit_field["id"], "recordId": row["__id"]})
            result.setdefault("extra", {})["searchHitIndex"] = hits
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
        old_row = dict(row)
        updates = {}
        for key, value in body.record.fields.items():
            field = self._field_by_key(fields, key, body.fieldKeyType)
            updates[field["db_field_name"]] = self._to_db_value(field, value)
        if updates:
            await repository.update_row(table["base_id"], table_id, record_id, updates)
            row = await repository.fetch_row(table["base_id"], table_id, record_id, columns) or row
            await self._write_history(table_id, record_id, fields, old_row, row)
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
            old_row = dict(row)
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
                await self._write_history(table_id, record_id, fields, old_row, row)
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

    # ---- record history ------------------------------------------------------

    async def _write_history(
        self,
        table_id: str,
        record_id: str,
        fields: list[dict[str, Any]],
        old_row: dict[str, Any],
        new_row: dict[str, Any],
    ) -> None:
        # one row per changed field; equal values (incl. no-op patches) skip.
        user_id = cls.get("user.id")
        rows = []
        for field in fields:
            column = field["db_field_name"]
            entry = build_history_row(
                table_id=table_id,
                record_id=record_id,
                field_id=field["id"],
                name=field["name"],
                field_type=field["type"],
                options_raw=field.get("options"),
                cell_value_type=field["cell_value_type"],
                before=self._from_db_value(field, old_row.get(column)),
                after=self._from_db_value(field, new_row.get(column)),
                user_id=user_id,
            )
            if entry:
                rows.append(entry)
        if rows:
            await repository.insert_history(rows)

    async def get_history(
        self,
        table_id: str,
        record_id: str | None,
        cursor: str | None,
        start_date: str | None,
        end_date: str | None,
        field_ids: list[str] | None,
        created_by_ids: list[str] | None,
    ) -> dict[str, Any]:
        await self._load_context(table_id)
        cursor_time = cursor_id = None
        if cursor and cursor.startswith("chs1:"):
            try:
                decoded = json.loads(base64.b64decode(cursor[5:]))
                cursor_time, cursor_id = decoded["t"], decoded["id"]
            except (ValueError, KeyError, json.JSONDecodeError):
                cursor_time = cursor_id = None
        rows = await repository.list_history(
            table_id,
            record_id=record_id,
            start_date=start_date,
            end_date=end_date,
            field_ids=field_ids,
            created_by_ids=created_by_ids,
            cursor_time=cursor_time,
            cursor_id=cursor_id,
            limit=21,
        )
        result: dict[str, Any] = {}
        if len(rows) > 20:
            rows = rows[:20]
            last = rows[-1]
            result["nextCursor"] = "chs1:" + base64.b64encode(
                json.dumps({"t": _iso(last["created_time"]), "id": last["id"]}).encode()
            ).decode()
        created_by = list({r["created_by"] for r in rows})
        users = await repository.get_users_by_ids(created_by) if created_by else []
        user_map = {
            u["id"]: {
                "id": u["id"],
                "name": u["name"],
                "email": u["email"],
                "avatar": get_public_full_storage_url(u["avatar"]) if u["avatar"] else None,
            }
            for u in users
        }
        result["historyList"] = [
            {
                "id": r["id"],
                "tableId": r["table_id"],
                "recordId": r["record_id"],
                "fieldId": r["field_id"],
                "before": json.loads(r["before"]),
                "after": json.loads(r["after"]),
                "createdTime": _iso(r["created_time"]),
                "createdBy": r["created_by"],
            }
            for r in rows
        ]
        result["userMap"] = user_map
        return result

    # ---- form submit ---------------------------------------------------------

    async def form_submit(self, table_id: str, body: RecordSubmitBody) -> dict[str, Any]:
        table, fields = await self._load_context(table_id)
        view = await get_view_row(table_id, body.viewId)
        if view is None:
            raise ApiError(
                "Invalid ViewId",
                HttpErrorCode.VALIDATION_ERROR,
                {"domainCode": "validation.invalid", "domainTags": ["validation"]},
            )
        if view["type"] != "form":
            raise ApiError(
                "View is not a form",
                HttpErrorCode.RESTRICTED_RESOURCE,
                {"domainCode": "view.type_not_form", "domainTags": ["forbidden"]},
            )
        column_meta = json.loads(view.get("column_meta") or "{}")
        field_id_set = {f["id"] for f in fields}
        visible_ids = {
            fid for fid, meta in column_meta.items() if meta.get("visible") is True
        } & field_id_set
        if (not visible_ids and body.fields) or any(
            key not in visible_ids for key in body.fields
        ):
            raise ApiError(
                "The form contains hidden fields, submission not allowed.",
                HttpErrorCode.RESTRICTED_RESOURCE,
                {
                    "domainCode": "view.hidden_fields_submission_not_allowed",
                    "domainTags": ["forbidden"],
                },
            )
        record_id = new_id(IdPrefix.RECORD)
        user_id = cls.get("user.id")
        values: dict[str, Any] = {
            "__id": record_id,
            "__created_by": user_id,
            "__last_modified_by": user_id,
            "__version": 1,
        }
        for key, value in body.fields.items():
            field = self._field_by_key(fields, key, "id")
            values[field["db_field_name"]] = self._to_db_value(field, value)
        await repository.insert_row(table["base_id"], table_id, values)
        return {"id": record_id, "fields": body.fields}

    async def get_status(self, table_id: str, record_id: str) -> dict[str, Any]:
        table, _fields = await self._load_context(table_id)
        row = await repository.fetch_row(table["base_id"], table_id, record_id, [])
        if row is None:
            raise _record_not_found(localization=True)
        return {"isDeleted": False, "isVisible": True}
