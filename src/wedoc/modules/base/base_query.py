"""Base query engine — ports features/base/base-query (BaseQueryService + parse/*).

Compiles a structured base query (from / where / groupBy / aggregation /
orderBy / select / limit / offset over one table) into SQL and returns the
``{rows, columns}`` view object the reference exposes. Reuses the record filter
and sort compilers, the aggregation statistic SQL, the field-VO serializer and
the raw-row executor so behaviour tracks the already-aligned surfaces.

Only a table id ``from`` is compiled here (the dashboard/plugin-panel chart
surface); nested sub-queries and joins are reported as unsupported the same way
a failing query is (``httpErrors.baseQuery.queryFailed``).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from ...core.errors import ApiError, HttpErrorCode
from ..aggregation import repository as agg_repository
from ..aggregation.service import (
    _agg_expr,
    _date_trunc_unit,
    _field_options,
    _field_timezone,
)
from ..field.repository import get_table_meta_by_id
from ..field.service import _field_vo
from ..record.cell_format import cell_value_to_string
from ..record.service import RecordService, _iso
from ..table import repository as table_repository

_MAX_LIMIT = 1000
# createdBy / lastModifiedBy carry the same {id,title} structured cell as user.
_USER_OR_LINK = {"link", "user", "createdBy", "lastModifiedBy"}


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _precision(field: dict[str, Any]) -> int:
    return int((_field_options(field).get("formatting") or {}).get("precision", 0) or 0)


def _table_not_found(table_id: str, base_id: str) -> ApiError:
    return ApiError(
        "Table not found",
        HttpErrorCode.NOT_FOUND,
        {
            "localization": {
                "i18nKey": "httpErrors.baseQuery.tableNotFound",
                "context": {"tableId": table_id, "baseId": base_id},
            }
        },
    )


def _query_failed(query: str, message: str) -> ApiError:
    return ApiError(
        "Query failed",
        HttpErrorCode.VALIDATION_ERROR,
        {
            "localization": {
                "i18nKey": "httpErrors.baseQuery.queryFailed",
                "context": {"query": query, "message": message},
            }
        },
    )


class _Column:
    """A column entry threaded through the parse pipeline (a field instance in
    the reference). ``out`` is the mutable public column id / row key; ``row`` is
    the source field row (``None`` for aggregation columns)."""

    __slots__ = ("id", "is_agg", "name", "out", "row", "select_src", "sort_row")

    def __init__(
        self,
        col_id: str,
        name: str,
        *,
        is_agg: bool,
        row: dict[str, Any] | None,
        sort_row: dict[str, Any] | None,
        select_src: str,
        out: str,
    ) -> None:
        self.id = col_id
        self.name = name
        self.select_src = select_src
        self.out = out
        self.is_agg = is_agg
        self.row = row
        self.sort_row = sort_row

class BaseQueryService:
    def __init__(self) -> None:
        self._records = RecordService()

    async def base_query(
        self, base_id: str, query: dict[str, Any], cell_format: str = "json"
    ) -> dict[str, Any]:
        table_id = query.get("from")
        if not isinstance(table_id, str):
            # nested sub-query source is not part of the chart surface.
            raise _query_failed(str(table_id), "Unsupported base query source")
        if query.get("join"):
            raise _query_failed("", "Unsupported base query join")

        table = await get_table_meta_by_id(table_id)
        if table is None or table["deleted_time"] is not None or table["base_id"] != base_id:
            raise _table_not_found(table_id, base_id)
        fields = await table_repository.list_field_rows(table_id)

        sql, params, columns_src = self._parse(base_id, table_id, query, fields)
        try:
            rows = await agg_repository.raw_rows(sql, params)
        except Exception as exc:  # mirror the reference catch → 400 queryFailed
            raise _query_failed(sql, str(exc)) from exc
        columns = self._columns(columns_src)
        return {"rows": self._rows(rows, columns_src, cell_format), "columns": columns}

    # ---- pipeline ---------------------------------------------------------

    def _parse(
        self,
        base_id: str,
        table_id: str,
        query: dict[str, Any],
        fields: list[dict[str, Any]],
    ) -> tuple[str, dict[str, Any], list[_Column]]:
        records = self._records
        field_by_id = {f["id"]: f for f in fields}
        field_map: dict[str, _Column] = {}
        for f in fields:
            field_map[f["id"]] = _Column(
                f["id"],
                f["name"],
                is_agg=False,
                row=f,
                sort_row=f,
                select_src=_quote_ident(f["db_field_name"]),
                out=f["id"],
            )

        params: dict[str, Any] = {}
        counter = [0]

        where_sql = ""
        if query.get("where"):
            converted = self._convert_filter(query["where"], field_by_id)
            compiled = records._compile_filter(fields, converted, params, counter)
            if compiled:
                where_sql = " WHERE " + compiled
        # groupBy → group-by expressions + group column entries (selectGroup).
        group_by_exprs: list[str] = []
        group_columns: list[tuple[_Column, str]] = []
        agg_group_ids: list[str] = []
        group_present = bool(query.get("groupBy"))
        for item in query.get("groupBy") or []:
            if item.get("type") == "aggregation":
                agg_group_ids.append(item["column"])
                group_by_exprs.append(_quote_ident(item["column"]))
                continue
            field = field_by_id.get(item["column"])
            if field is None:
                continue
            group_by, select_expr = self._group_exprs(field)
            group_by_exprs.append(group_by)
            column = _Column(
                field["id"],
                field["name"],
                is_agg=False,
                row=field,
                sort_row=field,
                select_src=select_expr,
                out=field["id"],
            )
            group_columns.append((column, select_expr))

        limit = query.get("limit")
        if isinstance(limit, (int, float)) and limit > 0:
            limit = min(int(limit), _MAX_LIMIT)
        else:
            limit = _MAX_LIMIT
        offset = query.get("offset")
        offset = int(offset) if isinstance(offset, (int, float)) and offset else None

        # aggregation → statistic selects + aggregation column entries.
        agg_selects: list[str] = []
        agg_present = bool(query.get("aggregation"))
        for item in query.get("aggregation") or []:
            field = field_by_id.get(item["column"])
            if field is None:
                raise _query_failed("", f"aggregation column {item['column']} not found")
            func = item["statisticFunc"]
            agg_id = f"{item['column']}_{func}"
            expr = _agg_expr(_quote_ident(field["db_field_name"]), func)
            agg_selects.append(f'{expr} AS {_quote_ident(agg_id)}')
            field_map[agg_id] = _Column(
                agg_id,
                f"{field['name']}.{func}",
                is_agg=True,
                row=None,
                sort_row=field,
                select_src="",
                out=agg_id,
            )
        # orderBy runs before select: it references the pre-select field/agg
        # instances, so an aggregation order resolves to its source column (the
        # reference does the same, which is why grouped agg orders can fail).
        order_terms: list[str] = []
        for item in query.get("orderBy") or []:
            entry = field_map.get(item["column"])
            if entry is None or entry.sort_row is None:
                continue
            term = self._sort_term(records, fields, entry.sort_row["id"], item.get("order"))
            if term:
                order_terms.append(term)
        order_sql = (" ORDER BY " + ", ".join(order_terms)) if order_terms else ""

        # select: mutate the field map exactly as QuerySelect does.
        select = query.get("select")
        agg_columns = [
            f"{a['column']}_{a['statisticFunc']}" for a in (query.get("aggregation") or [])
        ]
        group_selects: list[str] = [expr for _, expr in group_columns]
        for agg_alias in agg_group_ids:
            group_selects.append(f'{_quote_ident(agg_alias)} AS {_quote_ident(agg_alias)}')

        if agg_present or group_present:
            allow = set(agg_columns)
            field_map = {k: v for k, v in field_map.items() if k in allow}

        other_selects: list[str] = []
        if select is not None:
            for cur in select:
                entry = field_map.get(cur["column"])
                if entry is not None and not entry.is_agg:
                    alias = (cur.get("alias") or entry.id).replace("?", "_")
                    other_selects.append(f'{entry.select_src} AS {_quote_ident(alias)}')
                    entry.name = alias
                    entry.out = alias
                elif entry is not None and cur["column"] not in agg_columns:
                    other_selects.append(_quote_ident(cur["column"]))
                elif entry is not None:
                    entry.out = cur["column"]
            selected = {cur["column"] for cur in select}
            for key in list(field_map.keys()):
                if key not in selected:
                    if key in agg_columns:
                        field_map[key].out = key
                        continue
                    del field_map[key]
        else:
            for entry in list(field_map.values()):
                if not entry.is_agg:
                    other_selects.append(f'{entry.select_src} AS {_quote_ident(entry.id)}')
                    entry.out = entry.id
                else:
                    entry.out = entry.id

        final_columns: list[_Column] = list(field_map.values())
        seen = {c.id for c in final_columns}
        for column, _ in group_columns:
            if column.id in seen:
                final_columns = [c for c in final_columns if c.id != column.id]
            final_columns.append(column)

        select_clause = ", ".join(agg_selects + group_selects + other_selects) or "*"
        sql = f'SELECT {select_clause} FROM {_quote_ident(base_id)}.{_quote_ident(table_id)}'
        sql += where_sql
        if group_by_exprs:
            sql += " GROUP BY " + ", ".join(group_by_exprs)
        sql += order_sql
        sql += f" LIMIT {limit}"
        if offset:
            sql += f" OFFSET {offset}"
        return sql, params, final_columns
    # ---- helpers ----------------------------------------------------------

    def _convert_filter(
        self, qfilter: dict[str, Any], field_by_id: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        # ports convertQueryFilterToFilter: remap column→fieldId, 404 unknown.
        filter_set: list[dict[str, Any]] = []
        for item in qfilter.get("filterSet") or []:
            if "filterSet" in item:
                filter_set.append(self._convert_filter(item, field_by_id))
                continue
            if item["column"] not in field_by_id:
                raise ApiError(
                    f"Field {item['column']} not found",
                    HttpErrorCode.NOT_FOUND,
                    {"localization": {"i18nKey": "httpErrors.field.notFound"}},
                )
            filter_set.append(
                {
                    "isSymbol": False,
                    "fieldId": item["column"],
                    "operator": item["operator"],
                    "value": item.get("value"),
                }
            )
        return {"filterSet": filter_set, "conjunction": qfilter.get("conjunction")}

    def _group_exprs(self, field: dict[str, Any]) -> tuple[str, str]:
        """(group-by expression, select expression) for one group field, ports
        group-query.postgres.ts + QuerySelect.selectGroup."""
        col = _quote_ident(field["db_field_name"])
        fid = _quote_ident(field["id"])
        cvt = field["cell_value_type"]
        multiple = bool(field.get("is_multiple_cell_value"))
        if field["type"] in _USER_OR_LINK:
            select_expr = f'MAX({col}::text) AS {fid}'
            if multiple:
                group_by = (
                    f"jsonb_path_query_array({col}::jsonb, '$[*].id')::text, "
                    f"jsonb_path_query_array({col}::jsonb, '$[*].title')::text"
                )
            else:
                group_by = f"{col}::jsonb ->> 'id', {col}::jsonb ->> 'title'"
            return group_by, select_expr
        if multiple and cvt == "dateTime":
            group_by = self._multi_date_expr(field, col)
        elif multiple and cvt == "number":
            group_by = self._multi_number_expr(field, col)
        elif cvt == "number":
            precision = _precision(field)
            group_by = f"ROUND({col}::numeric, {precision}::int)::float"
        elif cvt == "dateTime":
            tz = _field_timezone(field)
            unit = _date_trunc_unit(field)
            group_by = f"TIMEZONE('{tz}', DATE_TRUNC('{unit}', TIMEZONE('{tz}', {col})))"
        else:
            group_by = col
        return group_by, f'{group_by} AS {fid}'

    def _multi_number_expr(self, field: dict[str, Any], col: str) -> str:
        precision = _precision(field)
        return (
            f"(SELECT to_jsonb(array_agg(ROUND(elem::numeric, {precision}::int))) "
            f"FROM jsonb_array_elements_text({col}::jsonb) as elem)"
        )

    def _multi_date_expr(self, field: dict[str, Any], col: str) -> str:
        tz = _field_timezone(field)
        unit = _date_trunc_unit(field)
        cast = "CAST(elem AS timestamp with time zone)"
        elem = f"TIMEZONE('{tz}', DATE_TRUNC('{unit}', TIMEZONE('{tz}', {cast})))"
        return (
            f"(SELECT to_jsonb(array_agg({elem})) "
            f"FROM jsonb_array_elements_text({col}::jsonb) as elem)"
        )

    def _sort_term(
        self, records: RecordService, fields: list[dict[str, Any]], field_id: str, order: Any
    ) -> str:
        compiled = records._compile_sort(fields, [{"fieldId": field_id, "order": order}])
        prefix = " ORDER BY "
        return compiled[len(prefix):] if compiled.startswith(prefix) else ""
    # ---- output shaping ---------------------------------------------------

    def _columns(self, columns: list[_Column]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for column in columns:
            if column.is_agg:
                out.append({"column": column.out, "name": column.name, "type": "aggregation"})
                continue
            assert column.row is not None
            vo = _field_vo({**column.row, "db_field_name": column.out, "name": column.name})
            out.append(
                {
                    "column": column.out,
                    "name": column.name,
                    "type": "field",
                    "fieldSource": vo,
                }
            )
        return out

    def _rows(
        self, db_rows: list[dict[str, Any]], columns: list[_Column], cell_format: str
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for row in db_rows:
            res: dict[str, Any] = {}
            for column in columns:
                key = column.out
                value = row.get(key)
                if column.is_agg:
                    res[key] = self._agg_value(value)
                    continue
                field = column.row
                assert field is not None
                cell = self._records._from_db_value(field, value)
                if isinstance(cell, (int, float)) and not isinstance(cell, bool):
                    res[key] = cell
                    continue
                if cell is not None:
                    res[key] = (
                        cell_value_to_string(field, cell) if cell_format == "text" else cell
                    )
            result.append(res)
        return result

    @staticmethod
    def _agg_value(value: Any) -> Any:
        # dbRows2Rows passes aggregation cells through untouched (bigint→number);
        # PG numeric arrives as Decimal and serializes like the reference Decimal
        # (trailing zeros trimmed, plain notation).
        if value is None or isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value) if value.is_integer() else value
        if isinstance(value, Decimal):
            return format(value.normalize(), "f")
        if isinstance(value, datetime):
            return _iso(value)
        return value

