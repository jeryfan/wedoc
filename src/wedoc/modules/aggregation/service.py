"""aggregation domain service — ports features/aggregation.

Footer/statistic aggregations, row count, record/search index, group points
and the calendar daily collection, all scoped by the same view-filter merge
as the record list. Statistic values are computed in SQL mirroring the
reference aggregation functions, so PG numeric formatting (percent* as
decimal strings with trailing zeros trimmed, count/sum as numbers) matches
byte-for-byte.
"""

import json
from datetime import datetime
from decimal import Decimal
from typing import Any

from ...core.errors import ApiError, HttpErrorCode
from ..record.repository import count_rows, list_rows
from ..record.service import RecordService, _iso
from ..record.tql import TqlParseError, parse_tql
from ..view.repository import get_view_row
from . import repository

PERCENT_FUNCS = {
    "percentEmpty",
    "percentFilled",
    "percentUnique",
    "percentChecked",
    "percentUnChecked",
}

DEFAULT_TIMEZONE = "Asia/Shanghai"


def _search_required() -> ApiError:
    return ApiError(
        "Search query is required",
        HttpErrorCode.VALIDATION_ERROR,
        {"localization": {"i18nKey": "httpErrors.aggregation.searchQueryRequired"}},
    )


def _invalid_start_field() -> ApiError:
    return ApiError(
        "Invalid start date field id",
        HttpErrorCode.VALIDATION_ERROR,
        {"localization": {"i18nKey": "httpErrors.aggregation.invalidStartDateFieldId"}},
    )


def _invalid_end_field() -> ApiError:
    return ApiError(
        "Invalid end date field id",
        HttpErrorCode.VALIDATION_ERROR,
        {"localization": {"i18nKey": "httpErrors.aggregation.invalidEndDateFieldId"}},
    )


def _string2hash(value: str) -> int:
    # djb2-xor (utils/string-hash.ts), iterated from the last char.
    hash_value = 5381
    for ch in reversed(value):
        hash_value = ((hash_value * 33) ^ ord(ch)) & 0xFFFFFFFF
    return hash_value


def _js_number(value: float) -> int | float:
    # JS String(Number) drops the fraction for integral floats.
    return int(value) if value.is_integer() else value


def _field_options(field: dict[str, Any]) -> dict[str, Any]:
    options = field.get("options")
    if isinstance(options, str):
        return json.loads(options or "{}")
    return options or {}


def _field_timezone(field: dict[str, Any]) -> str:
    formatting = _field_options(field).get("formatting") or {}
    return formatting.get("timeZone") or DEFAULT_TIMEZONE


def _auto_number_tail(order_sql: str) -> str:
    # the reference appends the creation-order column as the final sort key.
    return (order_sql + ', "__auto_number" ASC') if order_sql else ' ORDER BY "__auto_number" ASC'


def _sanitize_filter(filter_obj: dict[str, Any] | None) -> dict[str, Any] | None:
    # the reference filter schema silently drops is/isNot with a null value
    # (match-all); TQL IS NULL is unaffected (different parser path).
    if not isinstance(filter_obj, dict):
        return filter_obj
    items = []
    for item in filter_obj.get("filterSet") or []:
        if "filterSet" in item:
            nested = _sanitize_filter(item)
            if nested is not None:
                items.append(nested)
            continue
        if item.get("operator") in ("is", "isNot") and item.get("value") is None:
            continue
        items.append(item)
    if not items:
        return None
    return {**filter_obj, "filterSet": items}


def _valid_statistic_funcs(field: dict[str, Any]) -> list[str]:
    # models/aggregation/statistic.ts — the message lists these in this order.
    field_type = field["type"]
    if field_type in ("user", "createdBy", "lastModifiedBy"):
        funcs = ["count", "empty", "filled"]
        if not field.get("is_multiple_cell_value"):
            funcs += ["unique"]
        funcs += ["percentEmpty", "percentFilled"]
        if not field.get("is_multiple_cell_value"):
            funcs += ["percentUnique"]
        return funcs
    cell_value_type = field["cell_value_type"]
    if cell_value_type == "number":
        return [
            "sum", "average", "min", "max", "count", "empty", "filled", "unique",
            "percentEmpty", "percentFilled", "percentUnique",
        ]
    if cell_value_type == "dateTime":
        return [
            "count", "empty", "filled", "unique", "percentEmpty", "percentFilled",
            "percentUnique", "earliestDate", "latestDate", "dateRangeOfDays",
            "dateRangeOfMonths",
        ]
    if cell_value_type == "boolean":
        return ["count", "checked", "unChecked", "percentChecked", "percentUnChecked"]
    return [
        "count", "empty", "filled", "unique", "percentEmpty", "percentFilled",
        "percentUnique",
    ]


def _agg_expr(column: str, func: str) -> str:
    if func == "count":
        return "COUNT(*)"
    if func == "empty":
        return f"COUNT(*) - COUNT({column})"
    if func == "filled":
        return f"COUNT({column})"
    if func == "unique":
        return f"COUNT(DISTINCT {column})"
    if func == "max":
        return f"MAX({column})"
    if func == "min":
        return f"MIN({column})"
    if func == "sum":
        return f"SUM({column})"
    if func == "average":
        return f"AVG({column})"
    if func == "percentEmpty":
        return f"((COUNT(*) - COUNT({column})) * 1.0 / GREATEST(COUNT(*), 1)) * 100"
    if func == "percentFilled":
        return f"(COUNT({column}) * 1.0 / GREATEST(COUNT(*), 1)) * 100"
    if func == "percentUnique":
        return f"(COUNT(DISTINCT {column}) * 1.0 / GREATEST(COUNT(*), 1)) * 100"
    if func == "checked":
        return f"SUM(CASE WHEN {column} = true THEN 1 ELSE 0 END)"
    if func == "unChecked":
        return f"SUM(CASE WHEN {column} = false OR {column} IS NULL THEN 1 ELSE 0 END)"
    if func == "percentChecked":
        return (
            f"(SUM(CASE WHEN {column} = true THEN 1 ELSE 0 END)"
            " * 1.0 / GREATEST(COUNT(*), 1)) * 100"
        )
    if func == "percentUnChecked":
        return (
            f"(SUM(CASE WHEN {column} = false OR {column} IS NULL THEN 1 ELSE 0 END)"
            " * 1.0 / GREATEST(COUNT(*), 1)) * 100"
        )
    if func == "earliestDate":
        return f"MIN({column})"
    if func == "latestDate":
        return f"MAX({column})"
    if func == "dateRangeOfDays":
        return f"extract(DAY FROM (MAX({column}) - MIN({column})))::INTEGER"
    if func == "dateRangeOfMonths":
        return f"CONCAT(MAX({column}), ',', MIN({column}))"
    raise ApiError(f"Unknown function {func} for aggregation", HttpErrorCode.INTERNAL_SERVER_ERROR)


def _date_trunc_unit(field: dict[str, Any]) -> str:
    formatting = _field_options(field).get("formatting") or {}
    preset = formatting.get("date") or "YYYY-MM-DD"
    if preset == "Y":
        return "year"
    if preset in ("M", "YM"):
        return "month"
    return "minute" if formatting.get("time") not in (None, "None") else "day"


class AggregationService:
    def __init__(self) -> None:
        self._records = RecordService()

    # ---- shared helpers ----------------------------------------------------

    async def _load(self, table_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        return await self._records._load_context(table_id)

    async def _merged_filter(
        self,
        table_id: str,
        view_id: str | None,
        filter_param: dict[str, Any] | None,
        ignore_view_query: bool,
    ) -> dict[str, Any] | None:
        view_filter = None
        if view_id and not ignore_view_query:
            view = await get_view_row(table_id, view_id)
            if view is None:
                raise ApiError(
                    f"View {view_id} not found",
                    HttpErrorCode.NOT_FOUND,
                    {"localization": {"i18nKey": "httpErrors.view.notFound"}},
                )
            view_filter = json.loads(view["filter"]) if view.get("filter") else None
        if view_filter and filter_param:
            return {"conjunction": "and", "filterSet": [view_filter, filter_param]}
        return filter_param or view_filter

    def _compile_where(
        self,
        fields: list[dict[str, Any]],
        filter_obj: dict[str, Any] | None,
        search: list[Any] | None,
        exact_only: bool,
    ) -> tuple[str, dict[str, Any]]:
        params: dict[str, Any] = {}
        counter = [0]
        clauses = []
        filter_obj = _sanitize_filter(filter_obj)
        if filter_obj:
            compiled = self._records._compile_filter(fields, filter_obj, params, counter)
            if compiled:
                clauses.append(compiled)
        if exact_only and search is not None and len(search) >= 3 and search[2] in ("true", True):
            clause = self._search_clause(fields, search, params, counter)
            if clause:
                clauses.append(clause)
        return (" WHERE " + " AND ".join(f"({c})" for c in clauses)) if clauses else "", params

    def _search_clause(
        self,
        fields: list[dict[str, Any]],
        search: list[Any],
        params: dict[str, Any],
        counter: list[int],
    ) -> str | None:
        # substring search mirroring SearchQueryPostgres: ILIKE with escaped
        # wildcards; number fields round to their display precision first.
        value = str(search[0])
        escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        refs = str(search[1]).split(",") if len(search) >= 2 and search[1] else []
        targets = []
        for ref in refs:
            field = self._records._resolve_field(fields, ref)
            if field is not None and field not in targets:
                targets.append(field)
        if not targets:
            # an empty or unresolvable field ref selects no search fields, as in
            # getSearchFields; search-count/-index then answer {count: 0} / null.
            return None
        clauses = []
        for field in targets:
            column = f'"{field["db_field_name"]}"'
            if field["cell_value_type"] == "number":
                precision = (_field_options(field).get("formatting") or {}).get("precision", 0)
                expr = f"ROUND({column}::numeric, {int(precision)})::text"
            else:
                expr = column
            param = f"s{counter[0]}"
            counter[0] += 1
            params[param] = f"%{escaped}%"
            clauses.append(f"{expr} ILIKE :{param} ESCAPE '\\'")
        return " OR ".join(clauses)

    @staticmethod
    def _table_ref(table: dict[str, Any]) -> str:
        return f'"{table["base_id"]}"."{table["id"]}"'

    @staticmethod
    def _convert_value(value: Any, func: str) -> Any:
        # formatConvertValue: PG numeric arrives as Decimal and is serialized
        # like the reference's decimal.js (trailing zeros trimmed, no exponent).
        if value is None:
            return 0 if func in PERCENT_FUNCS else None
        if func == "dateRangeOfMonths" and isinstance(value, str):
            return AggregationService._month_range(value)
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            # JS JSON.stringify renders integral floats without the fraction.
            return int(value) if value.is_integer() else value
        if isinstance(value, Decimal):
            return format(value.normalize(), "f")
        if isinstance(value, datetime):
            return _iso(value)
        return str(value)

    @staticmethod
    def _month_range(concat: str) -> int:
        # "max,min" (PG timestamptz text) → dayjs diff in whole months.
        try:
            left, right = concat.split(",")
            later = datetime.fromisoformat(left.strip().replace(" ", "T").split("+")[0])
            earlier = datetime.fromisoformat(right.strip().replace(" ", "T").split("+")[0])
        except (ValueError, IndexError):
            return 0
        months = (later.year - earlier.year) * 12 + (later.month - earlier.month)
        if (later.day, later.hour, later.minute, later.second) < (
            earlier.day,
            earlier.hour,
            earlier.minute,
            earlier.second,
        ):
            months -= 1
        return months

    def _statistic_selects(
        self, statistic_fields: list[tuple[dict[str, Any], str]]
    ) -> str:
        parts = []
        for field, func in statistic_fields:
            column = f'"{field["db_field_name"]}"'
            parts.append(f'{_agg_expr(column, func)} AS "{field["id"]}_{func}"')
        return ", ".join(parts)

    # ---- GET / (aggregation) -------------------------------------------------

    async def get_aggregation(
        self,
        table_id: str,
        field_stats: list[tuple[str, list[str]]] | None,
        filter_param: dict[str, Any] | None,
        tql: str | None,
        search: list[Any] | None,
        view_id: str | None,
        group_by: list[dict[str, Any]] | None,
        ignore_view_query: bool,
    ) -> dict[str, Any]:
        table, fields = await self._load(table_id)
        filter_obj = await self._merged_filter(table_id, view_id, filter_param, ignore_view_query)
        if tql:
            filter_obj = self._parse_tql(tql)

        statistic_fields: list[tuple[dict[str, Any], str]] = []
        if field_stats:
            requested: list[tuple[str, str]] = []
            for func, field_ids in field_stats:
                for field_id in field_ids:
                    field = self._records._resolve_field(fields, field_id)
                    if field is None:
                        raise ApiError(
                            f"field: '{field_id}' is invalid", HttpErrorCode.VALIDATION_ERROR
                        )
                    valid = _valid_statistic_funcs(field)
                    if func not in valid:
                        raise ApiError(
                            f"field: '{field_id}', aggregation func: '{func}' is invalid, "
                            f"Only the following func are allowed: [{','.join(valid)}]",
                            HttpErrorCode.VALIDATION_ERROR,
                        )
                    if (func, field["id"]) not in requested:
                        requested.append((func, field["id"]))
            # field creation order, funcs in request order within each field
            for field in fields:
                statistic_fields.extend(
                    (field, func) for func, fid in requested if fid == field["id"]
                )
        elif view_id and not ignore_view_query:
            view = await get_view_row(table_id, view_id)
            if view is None:
                raise ApiError(
                    f"View {view_id} not found",
                    HttpErrorCode.NOT_FOUND,
                    {"localization": {"i18nKey": "httpErrors.view.notFound"}},
                )
            column_meta = json.loads(view["column_meta"]) if view.get("column_meta") else {}
            for field in fields:
                meta = column_meta.get(field["id"]) or {}
                func = meta.get("statisticFunc")
                if func and meta.get("hidden") is not True:
                    statistic_fields.append((field, func))

        if not statistic_fields:
            return {"aggregations": []}

        group_fields = []
        for item in group_by or []:
            field = self._records._resolve_field(fields, item.get("fieldId", ""))
            if field is not None:
                group_fields.append((field, item.get("order", "asc")))
        group_fields = group_fields[:3]

        where_sql, params = self._compile_where(fields, filter_obj, search, exact_only=True)
        selects = self._statistic_selects(statistic_fields)

        aggregations: list[dict[str, Any]] = []
        if group_fields:
            # totals plus one grouped query per groupBy prefix
            totals = await repository.raw_rows(
                f"SELECT {selects} FROM {self._table_ref(table)}{where_sql}", params
            )
            totals_row = totals[0] if totals else {}
        else:
            totals_row = (
                await repository.raw_rows(
                    f"SELECT {selects} FROM {self._table_ref(table)}{where_sql}", params
                )
            )[0]

        grouped_maps: dict[str, dict[str, Any]] = {}
        for depth in range(len(group_fields)):
            prefix = group_fields[: depth + 1]
            group_sql = self._grouped_query(table, selects, prefix, where_sql)
            rows = await repository.raw_rows(group_sql, params)
            for row in rows:
                key_parts = []
                for field, _ in prefix:
                    raw = row[field["db_field_name"]]
                    key_parts.append(self._stringify_group_value(raw))
                flag = f"{prefix[-1][0]['id']}_" + "_".join(key_parts)
                group_id = str(_string2hash(flag))
                for field, func in statistic_fields:
                    agg_key = f"{field['id']}_{func}"
                    entry = grouped_maps.setdefault(
                        field["id"], {"fieldId": field["id"], "total": None, "group": {}}
                    )
                    entry["group"][group_id] = {
                        "value": self._convert_value(row[agg_key], func),
                        "aggFunc": func,
                    }

        for field, func in statistic_fields:
            agg_key = f"{field['id']}_{func}"
            total = self._convert_value(totals_row.get(agg_key), func)
            if field["id"] in grouped_maps:
                entry = grouped_maps[field["id"]]
                entry["total"] = {"value": total, "aggFunc": func}
                aggregations.append(entry)
            else:
                aggregations.append(
                    {"fieldId": field["id"], "total": {"value": total, "aggFunc": func}}
                )
        # keep the reference's key order: fieldId, total, group
        for item in aggregations:
            item["group"] = item.pop("group") if "group" in item else None
            if item["group"] is None:
                del item["group"]
        return {"aggregations": aggregations}

    def _parse_tql(self, tql: str) -> dict[str, Any]:
        try:
            return parse_tql(tql)
        except TqlParseError as exc:
            raise ApiError(f"TQL parse error, {exc}", HttpErrorCode.VALIDATION_ERROR) from None

    def _group_expr(self, field: dict[str, Any]) -> str:
        column = f'"{field["db_field_name"]}"'
        if field["cell_value_type"] == "number":
            precision = (_field_options(field).get("formatting") or {}).get("precision", 0)
            return f"ROUND({column}::numeric, {int(precision)})::float"
        if field["cell_value_type"] == "boolean":
            # the reference groups unchecked cells with the null bucket.
            return f"CASE WHEN {column} IS TRUE THEN TRUE ELSE NULL END"
        if field["cell_value_type"] == "dateTime":
            unit = _date_trunc_unit(field)
            tz = _field_timezone(field)
            return f"TIMEZONE('{tz}', DATE_TRUNC('{unit}', TIMEZONE('{tz}', {column})))"
        return column

    def _group_select_expr(self, field: dict[str, Any]) -> str:
        # link fields group on the denormalized {id,title} cell (db-provider
        # group-query json branch). Single-value selects the rebuilt object
        # (null when the cell is empty); multi-value selects the group's array.
        column = f'"{field["db_field_name"]}"'
        if field["type"] == "link":
            if field.get("is_multiple_cell_value"):
                return f"(jsonb_agg({column}::jsonb) -> 0)"
            return (
                f"CASE WHEN {column}::jsonb ->> 'id' IS NULL"
                f" AND {column}::jsonb ->> 'title' IS NULL THEN NULL"
                f" ELSE jsonb_build_object('id', {column}::jsonb ->> 'id',"
                f" 'title', {column}::jsonb ->> 'title') END"
            )
        return self._group_expr(field)

    def _group_by_exprs(self, field: dict[str, Any]) -> list[str]:
        column = f'"{field["db_field_name"]}"'
        if field["type"] == "link":
            if field.get("is_multiple_cell_value"):
                return [
                    f"jsonb_path_query_array({column}::jsonb,'$[*].id')::text",
                    f"jsonb_path_query_array({column}::jsonb,'$[*].title')::text",
                ]
            return [f"{column}::jsonb ->> 'id'", f"{column}::jsonb ->> 'title'"]
        return [self._group_expr(field)]

    def _grouped_query(
        self,
        table: dict[str, Any],
        selects: str,
        prefix: list[tuple[dict[str, Any], str]],
        where_sql: str,
    ) -> str:
        select_exprs = ", ".join(
            f'{self._group_select_expr(field)} AS "{field["db_field_name"]}"'
            for field, _ in prefix
        )
        group_by = ", ".join(
            expr for field, _ in prefix for expr in self._group_by_exprs(field)
        )
        order_terms = ", ".join(
            self._group_order(field, order) for field, order in prefix
        )
        return (
            f"SELECT {select_exprs}, {selects} FROM {self._table_ref(table)}"
            f"{where_sql} GROUP BY {group_by} ORDER BY {order_terms}"
        )

    def _group_order(self, field: dict[str, Any], order: str) -> str:
        direction = "DESC" if order == "desc" else "ASC"
        nulls = "NULLS LAST" if direction == "DESC" else "NULLS FIRST"
        if field["type"] in ("singleSelect", "multipleSelect"):
            choices = _field_options(field).get("choices") or []
            names = [str(choice.get("name")) for choice in choices]
            if names:
                column = f'"{field["db_field_name"]}"'
                array_literal = "ARRAY[" + ", ".join(f"'{n}'" for n in names) + "]"
                return f"ARRAY_POSITION({array_literal}, {column}) {direction} {nulls}"
        if field["type"] == "link":
            # order groups by link title (record-query orderAggregateByGroup).
            column = f'"{field["db_field_name"]}"'
            if field.get("is_multiple_cell_value"):
                expr = f"jsonb_path_query_array({column}::jsonb,'$[*].title')::text"
            else:
                expr = f"{column}::jsonb ->> 'title'"
            return f"{expr} {direction} {nulls}"
        return f"{self._group_expr(field)} {direction} {nulls}"

    @staticmethod
    def _stringify_group_value(raw: Any) -> str:
        # convertValueToStringify; JS join renders null as "".
        if raw is None:
            return ""
        if isinstance(raw, bool):
            return "true" if raw else "false"
        if isinstance(raw, (int, float)):
            return str(_js_number(float(raw)))
        if isinstance(raw, datetime):
            return _iso(raw)
        return str(raw)

    # ---- GET /row-count -------------------------------------------------------

    async def get_row_count(
        self,
        table_id: str,
        filter_param: dict[str, Any] | None,
        tql: str | None,
        search: list[Any] | None,
        view_id: str | None,
        selected_record_ids: list[str] | None,
        ignore_view_query: bool,
    ) -> dict[str, Any]:
        table, fields = await self._load(table_id)
        filter_obj = await self._merged_filter(table_id, view_id, filter_param, ignore_view_query)
        if tql:
            filter_obj = self._parse_tql(tql)
        where_sql, params = self._compile_where(fields, filter_obj, search, exact_only=True)
        if selected_record_ids:
            clause = ", ".join(f":sid{i}" for i in range(len(selected_record_ids)))
            where_sql += f'{" AND" if where_sql else " WHERE"} "__id" IN ({clause})'
            params.update({f"sid{i}": rid for i, rid in enumerate(selected_record_ids)})
        row_count = await count_rows(table["base_id"], table_id, where_sql, params)
        return {"rowCount": row_count}

    # ---- GET /record-index ----------------------------------------------------

    async def get_record_index(
        self,
        table_id: str,
        record_id: str,
        filter_param: dict[str, Any] | None,
        order_by: list[dict[str, Any]] | None,
        view_id: str | None,
        ignore_view_query: bool,
    ) -> dict[str, Any] | None:
        table, fields = await self._load(table_id)
        filter_obj = await self._merged_filter(table_id, view_id, filter_param, ignore_view_query)
        view_sort = None
        if view_id and not ignore_view_query:
            view = await get_view_row(table_id, view_id)
            view_sort = (
                json.loads(view["sort"])["sortObjs"] if view and view.get("sort") else None
            )
        where_sql, params = self._compile_where(fields, filter_obj, None, exact_only=False)
        order_sql = self._records._compile_sort(fields, order_by) or self._records._compile_sort(
            fields, view_sort
        )
        order_sql = _auto_number_tail(order_sql)
        rows = await list_rows(table["base_id"], table_id, [], where_sql, params, order_sql)
        for index, row in enumerate(rows):
            if row["__id"] == record_id:
                return {"index": index}
        return None

    # ---- GET /search-count ----------------------------------------------------

    async def get_search_count(
        self,
        table_id: str,
        filter_param: dict[str, Any] | None,
        search: list[Any] | None,
        view_id: str | None,
        ignore_view_query: bool,
    ) -> dict[str, Any]:
        if search is None or len(search) < 2:
            raise _search_required()
        table, fields = await self._load(table_id)
        # missing views die before the filter merge in the reference stack.
        if view_id and not ignore_view_query:
            view = await get_view_row(table_id, view_id)
            if view is None:
                raise ApiError("Internal Server Error", HttpErrorCode.INTERNAL_SERVER_ERROR)
        filter_obj = await self._merged_filter(table_id, view_id, filter_param, ignore_view_query)
        where_sql, params = self._compile_where(fields, filter_obj, None, exact_only=False)
        counter = [0]
        clause = self._search_clause(fields, search, params, counter)
        if clause is None:
            return {"count": 0}
        where_sql += f'{" AND" if where_sql else " WHERE"} ({clause})'
        row_count = await count_rows(table["base_id"], table_id, where_sql, params)
        return {"count": row_count}

    # ---- GET /search-index ----------------------------------------------------

    async def get_search_index(
        self,
        table_id: str,
        filter_param: dict[str, Any] | None,
        search: list[Any] | None,
        take: int | None,
        skip: int,
        order_by: list[dict[str, Any]] | None,
        group_by: list[dict[str, Any]] | None,
        view_id: str | None,
        ignore_view_query: bool,
    ) -> list[dict[str, Any]] | None:
        if take is None:
            raise ApiError(
                'Validation error: Invalid input: expected number, received NaN at "take"',
                HttpErrorCode.VALIDATION_ERROR,
            )
        if take > 1000:
            raise ApiError(
                "The maximum search index result is 1000",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.aggregation.maxSearchIndexResult"}},
            )
        if search is None or len(search) < 2:
            raise _search_required()
        table, fields = await self._load(table_id)
        # missing views die before the filter merge in the reference stack.
        if view_id and not ignore_view_query:
            view = await get_view_row(table_id, view_id)
            if view is None:
                raise ApiError("Internal Server Error", HttpErrorCode.INTERNAL_SERVER_ERROR)
        filter_obj = await self._merged_filter(table_id, view_id, filter_param, ignore_view_query)

        where_sql, params = self._compile_where(fields, filter_obj, None, exact_only=False)
        search_params = dict(params)
        counter = [0]
        clause = self._search_clause(fields, search, search_params, counter)
        if clause is None:
            return None

        sort_items = [
            {"fieldId": item.get("fieldId", ""), "order": item.get("order", "asc")}
            for item in (group_by or []) + (order_by or [])
        ]
        sort_sql = _auto_number_tail(self._records._compile_sort(fields, sort_items))
        hit_where = where_sql + f'{" AND" if where_sql else " WHERE"} ({clause})'
        rows = await list_rows(
            table["base_id"],
            table_id,
            [],
            hit_where,
            search_params,
            sort_sql,
            limit=take,
            offset=skip,
        )
        # one hit entry per matched search field, in search-field order
        target_fields = []
        for ref in str(search[1]).split(","):
            field = self._records._resolve_field(fields, ref)
            if field is not None and field not in target_fields:
                target_fields.append(field)
        hits: list[tuple[str, str]] = []
        for row in rows:
            for field in target_fields:
                hits.append((row["__id"], field["id"]))
        if not hits:
            return None

        exact = len(search) >= 3 and search[2] in ("true", True)
        if exact:
            result = []
            seen: list[str] = []
            for record_id, field_id in hits:
                if record_id not in seen:
                    seen.append(record_id)
                result.append(
                    {"index": skip + len(seen), "fieldId": field_id, "recordId": record_id}
                )
            return result
        view_rows = await list_rows(
            table["base_id"],
            table_id,
            [],
            where_sql,
            params,
            sort_sql,
        )
        positions = {row["__id"]: i + 1 for i, row in enumerate(view_rows)}
        return [
            {
                "index": positions[record_id],
                "fieldId": field_id,
                "recordId": record_id,
            }
            for record_id, field_id in hits
        ]

    # ---- GET /group-points ------------------------------------------------------

    async def get_group_points(
        self,
        table_id: str,
        filter_param: dict[str, Any] | None,
        tql: str | None,
        search: list[Any] | None,
        group_by: list[dict[str, Any]] | None,
        collapsed_ids: list[str] | None,
        view_id: str | None,
        ignore_view_query: bool,
    ) -> list[dict[str, Any]]:
        table, fields = await self._load(table_id)
        group_fields = []
        for item in group_by or []:
            field = self._records._resolve_field(fields, item.get("fieldId", ""))
            if field is not None:
                group_fields.append((field, item.get("order", "asc")))
        group_fields = group_fields[:3]
        if not group_fields:
            return []
        filter_obj = await self._merged_filter(table_id, view_id, filter_param, ignore_view_query)
        if tql:
            filter_obj = self._parse_tql(tql)
        where_sql, params = self._compile_where(fields, filter_obj, search, exact_only=True)
        rows = await self._run_group_query(table, group_fields, where_sql, params)
        row_count = await count_rows(table["base_id"], table_id, where_sql, params)
        points, _refs = self._build_group_points(group_fields, rows, row_count, collapsed_ids)
        return points

    async def _run_group_query(
        self,
        table: dict[str, Any],
        group_fields: list[tuple[dict[str, Any], str]],
        where_sql: str,
        params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        select_exprs = ", ".join(
            f'{self._group_select_expr(field)} AS "{field["db_field_name"]}"'
            for field, _ in group_fields
        )
        group_by = ", ".join(
            expr for field, _ in group_fields for expr in self._group_by_exprs(field)
        )
        order_terms = ", ".join(
            self._group_order(field, order) for field, order in group_fields
        )
        return await repository.raw_rows(
            f"SELECT {select_exprs}, COUNT(*) AS __c FROM {self._table_ref(table)}"
            f"{where_sql} GROUP BY {group_by} ORDER BY {order_terms}",
            params,
        )

    def _build_group_points(
        self,
        group_fields: list[tuple[dict[str, Any], str]],
        rows: list[dict[str, Any]],
        row_count: int,
        collapsed_ids: list[str] | None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        sentinel = object()
        values: list[Any] = [sentinel, sentinel, sentinel]
        collapsed = set(collapsed_ids or [])
        points: list[dict[str, Any]] = []
        refs: list[dict[str, Any]] = []
        current_count = 0
        collapsed_depth = len(group_fields) + 1
        for row in rows:
            count = int(row["__c"])
            for depth, (field, _) in enumerate(group_fields):
                raw = row[field["db_field_name"]]
                stringified = self._stringify_group_value(raw)
                if values[depth] is not sentinel and values[depth] == stringified:
                    continue
                parts = [
                    values[i] if values[i] is not sentinel else None
                    for i in range(depth)
                ] + [stringified]
                flag = f"{field['id']}_" + "_".join("" if p is None else p for p in parts)
                group_id = str(_string2hash(flag))
                refs.append({"id": group_id, "depth": depth})
                if depth > collapsed_depth:
                    break
                collapsed_depth = len(group_fields) + 1
                values[depth] = stringified
                for idx in range(depth + 1, len(group_fields)):
                    values[idx] = sentinel
                is_collapsed = group_id in collapsed
                points.append(
                    {
                        "id": group_id,
                        "type": 0,
                        "depth": depth,
                        "value": self._header_value(field, raw),
                        "isCollapsed": is_collapsed,
                    }
                )
                if is_collapsed:
                    collapsed_depth = depth
            current_count += count
            if collapsed_depth != len(group_fields) + 1:
                continue
            points.append({"type": 1, "count": count})
        if current_count < row_count:
            points.append(
                {
                    "id": "unknown",
                    "type": 0,
                    "depth": 0,
                    "value": "Unknown",
                    "isCollapsed": False,
                }
            )
            points.append({"type": 1, "count": row_count - current_count})
        return points, refs

    async def group_points_for_records(
        self,
        table: dict[str, Any],
        fields: list[dict[str, Any]],
        group_by: list[dict[str, Any]] | None,
        where_sql: str,
        params: dict[str, Any],
        collapsed_ids: list[str] | None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
        # Shared with the record list: returns (groupPoints, allGroupHeaderRefs)
        # computed over the already-compiled filter (cursor/pagination excluded).
        group_fields = []
        for item in group_by or []:
            field = self._records._resolve_field(fields, item.get("fieldId", ""))
            if field is not None:
                group_fields.append((field, item.get("order", "asc")))
        group_fields = group_fields[:3]
        if not group_fields:
            return None
        rows = await self._run_group_query(table, group_fields, where_sql, params)
        row_count = await count_rows(table["base_id"], table["id"], where_sql, params)
        return self._build_group_points(group_fields, rows, row_count, collapsed_ids)

    @staticmethod
    def _header_value(field: dict[str, Any], raw: Any) -> Any:
        if raw is None:
            return None
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, float):
            return _js_number(raw)
        if isinstance(raw, datetime):
            return _iso(raw)
        return raw

    # ---- GET /calendar-daily-collection -----------------------------------------

    async def get_calendar_daily_collection(
        self,
        table_id: str,
        start_date: str,
        end_date: str,
        start_field_id: str,
        end_field_id: str,
        filter_param: dict[str, Any] | None,
        search: list[Any] | None,
        view_id: str | None,
        ignore_view_query: bool,
    ) -> dict[str, Any]:
        table, fields = await self._load(table_id)
        field_map = {f["id"]: f for f in fields}
        field_map.update({f["name"]: f for f in fields})
        start_field = field_map.get(start_field_id)
        if (
            start_field is None
            or start_field["cell_value_type"] != "dateTime"
            or start_field.get("is_multiple_cell_value")
        ):
            raise _invalid_start_field()
        end_field = field_map.get(end_field_id)
        if (
            end_field is None
            or end_field["cell_value_type"] != "dateTime"
            or end_field.get("is_multiple_cell_value")
        ):
            raise _invalid_end_field()

        filter_obj = await self._merged_filter(table_id, view_id, filter_param, ignore_view_query)
        where_sql, params = self._compile_where(fields, filter_obj, search, exact_only=True)
        tz = _field_timezone(start_field)
        start_col = f'"{start_field["db_field_name"]}"'
        end_col = f'"{end_field["db_field_name"]}"'
        ref = self._table_ref(table)
        # CAST() instead of ::bind adjacency: text() would swallow the bind name
        start_cast = "CAST(:start AS timestamptz)"
        end_cast = "CAST(:end AS timestamptz)"
        sql = f"""
        SELECT dates.date AS date, COUNT(*) AS count,
               (array_agg(q.__id ORDER BY q.{start_col}))[1:10] AS ids
        FROM {ref} q
        CROSS JOIN (
            SELECT date::date AS date
            FROM generate_series(
                ({start_cast} AT TIME ZONE :tz)::date,
                ({end_cast} AT TIME ZONE :tz)::date,
                '1 day'::interval
            ) AS date
        ) AS dates
        WHERE (q.{start_col}::timestamptz AT TIME ZONE :tz)::date
                  <= ({end_cast} AT TIME ZONE :tz)::date
          AND (COALESCE(q.{end_col}, q.{start_col})::timestamptz AT TIME ZONE :tz)::date
                  >= ({start_cast} AT TIME ZONE :tz)::date
          AND (q.{start_col}::timestamptz AT TIME ZONE :tz)::date <= dates.date
          AND (COALESCE(q.{end_col}, q.{start_col})::timestamptz AT TIME ZONE :tz)::date
                  >= dates.date
          {("AND " + where_sql[7:]) if where_sql else ""}
        GROUP BY dates.date
        ORDER BY dates.date ASC
        """
        # The client sends ISO instants (e.g. ...Z); bind aware datetimes so
        # asyncpg encodes them as timestamptz and the AT TIME ZONE :tz date
        # bucketing below stays correct (date.fromisoformat can't parse a time).
        bind = {
            "start": datetime.fromisoformat(start_date.replace("Z", "+00:00")),
            "end": datetime.fromisoformat(end_date.replace("Z", "+00:00")),
            "tz": tz,
            **params,
        }
        rows = await repository.raw_rows(sql, bind)
        count_map = {str(row["date"]): int(row["count"]) for row in rows}
        ids: list[str] = []
        for row in rows:
            for record_id in row["ids"] or []:
                if record_id not in ids:
                    ids.append(record_id)
        if not ids:
            return {"countMap": count_map, "records": []}
        all_rows = await list_rows(
            table["base_id"], table_id, [f["db_field_name"] for f in fields]
        )
        by_id = {row["__id"]: row for row in all_rows}
        records = [
            self._records._full_vo(by_id[record_id], fields, "id")
            for record_id in ids
            if record_id in by_id
        ]
        return {"countMap": count_map, "records": records}

    # ---- GET /selection -----------------------------------------------------------

    async def get_selection_aggregation(
        self,
        table_id: str,
        field_stats: list[tuple[str, list[str]]] | None,
        filter_param: dict[str, Any] | None,
        tql: str | None,
        search: list[Any] | None,
        view_id: str | None,
        order_by: list[dict[str, Any]] | None,
        group_by: list[dict[str, Any]] | None,
        skip: int,
        take: int | None,
        ignore_view_query: bool,
    ) -> dict[str, Any]:
        # same recipe as getAggregation, but computed over the [skip, skip+take)
        # slice of the filtered + sorted rows; groupBy folds into the ordering.
        table, fields = await self._load(table_id)
        filter_obj = await self._merged_filter(table_id, view_id, filter_param, ignore_view_query)
        if tql:
            filter_obj = self._parse_tql(tql)
        requested: list[tuple[str, str]] = []
        for func, field_ids in field_stats or []:
            for field_id in field_ids:
                field = self._records._resolve_field(fields, field_id)
                if field is None:
                    raise ApiError(
                        f"field: '{field_id}' is invalid", HttpErrorCode.VALIDATION_ERROR
                    )
                valid = _valid_statistic_funcs(field)
                if func not in valid:
                    raise ApiError(
                        f"field: '{field_id}', aggregation func: '{func}' is invalid, "
                        f"Only the following func are allowed: [{','.join(valid)}]",
                        HttpErrorCode.VALIDATION_ERROR,
                    )
                requested.append((func, field["id"]))
        statistic_fields = [
            (field, func)
            for field in fields
            for func, fid in requested
            if fid == field["id"]
        ]
        if not statistic_fields:
            return {"aggregations": []}

        where_sql, params = self._compile_where(fields, filter_obj, search, exact_only=True)
        view_sort = None
        if view_id and not ignore_view_query:
            view = await get_view_row(table_id, view_id)
            view_sort = (
                json.loads(view["sort"])["sortObjs"] if view and view.get("sort") else None
            )
        sort_items = [
            {"fieldId": item.get("fieldId", ""), "order": item.get("order", "asc")}
            for item in (group_by or []) + (order_by or [])
        ]
        order_sql = self._records._compile_sort(fields, sort_items)
        if not order_sql and view_sort:
            order_sql = self._records._compile_sort(fields, view_sort)
        order_sql = _auto_number_tail(order_sql)

        columns = sorted({f'"{f["db_field_name"]}"' for f, _ in statistic_fields})
        selects = self._statistic_selects(statistic_fields)
        if take is not None:
            inner = (
                f"SELECT {', '.join(columns)} FROM {self._table_ref(table)}"
                f"{where_sql}{order_sql} LIMIT :__limit OFFSET :__offset"
            )
            sql = f"SELECT {selects} FROM ({inner}) AS __slice"
            bind = {**params, "__limit": take, "__offset": skip}
        else:
            sql = f"SELECT {selects} FROM {self._table_ref(table)}{where_sql}"
            bind = params
        rows = await repository.raw_rows(sql, bind)
        row = rows[0] if rows else {}
        aggregations = [
            {
                "fieldId": field["id"],
                "total": {
                    "value": self._convert_value(row.get(f"{field['id']}_{func}"), func),
                    "aggFunc": func,
                },
            }
            for field, func in statistic_fields
        ]
        return {"aggregations": aggregations}
