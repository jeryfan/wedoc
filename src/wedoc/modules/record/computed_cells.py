"""Computed link cells: lookup + rollup value resolution at read time.

Ports the read-side aggregation of field-cte-visitor (lookup json_agg with
NULL filtering and link ordering) and the rollup functions. wedoc resolves
these synchronously from the foreign keys / denormalized link cells; the
reference materializes them asynchronously (BullMQ), so computed values are
correct here but only comparable to the reference after it settles.
"""

import json
from datetime import UTC, datetime
from typing import Any

from ...formula import FormulaError, evaluate, parse, reference_field_ids, to_cell_value
from ..field.repository import get_table_meta_by_id
from ..table import repository as table_repository
from . import repository
from .link_cells import link_input_ids

# conditionalRollup foreign query cap (ref conditional.constants
# CONDITIONAL_QUERY_MAX_LIMIT default). A query without an explicit limit still
# caps its rows here, and an explicit limit is clamped to it.
CONDITIONAL_QUERY_MAX_LIMIT = 5000


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_foreign_value(field: dict[str, Any], value: Any) -> Any:
    """Mirror RecordService._from_db_value for a foreign field's raw column."""
    if value is None:
        return None
    field_type = field["type"]
    if field_type in ("user", "attachment", "link") and isinstance(value, str):
        return json.loads(value)
    if field.get("is_multiple_cell_value") and isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    if field["cell_value_type"] == "dateTime" and isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


class ComputedContext:
    """Resolved metadata for a lookup/rollup field's link + source."""

    def __init__(
        self,
        link_field: dict[str, Any],
        source_field: dict[str, Any],
        foreign_table: dict[str, Any],
    ) -> None:
        self.link_field = link_field
        self.source_field = source_field
        self.foreign_base_id = foreign_table["base_id"]
        self.foreign_table_id = foreign_table["id"]


async def _load_context(
    all_fields: list[dict[str, Any]], options: dict[str, Any]
) -> ComputedContext | None:
    link_field = next(
        (f for f in all_fields if f["id"] == options.get("linkFieldId")), None
    )
    if link_field is None:
        return None
    foreign_table_id = options.get("foreignTableId")
    foreign_table = await get_table_meta_by_id(foreign_table_id) if foreign_table_id else None
    if foreign_table is None:
        return None
    foreign_fields = await table_repository.list_field_rows(foreign_table_id)
    source_field = next(
        (f for f in foreign_fields if f["id"] == options.get("lookupFieldId")), None
    )
    if source_field is None:
        return None
    return ComputedContext(link_field, source_field, foreign_table)


async def gather_link_values(
    ctx: ComputedContext,
    rows: list[dict[str, Any]],
    allowed_ids: set[str] | None = None,
) -> dict[str, list[Any]]:
    """Ordered, non-null foreign source values per record (link cell order).

    When ``allowed_ids`` is given (conditional rollup/lookup filter), only
    linked foreign records in that set contribute — mirroring the reference's
    ``CASE WHEN EXISTS (<foreign filter>) THEN value ELSE NULL END`` per row.
    """
    link_col = ctx.link_field["db_field_name"]
    ids_by_record: dict[str, list[str]] = {}
    all_ids: set[str] = set()
    for row in rows:
        raw = row.get(link_col)
        cell = json.loads(raw) if isinstance(raw, str) else raw
        ids = link_input_ids(cell)
        ids_by_record[row["__id"]] = ids
        all_ids.update(ids)
    value_map = await repository.fetch_column_by_ids(
        ctx.foreign_base_id, ctx.foreign_table_id, ctx.source_field["db_field_name"], list(all_ids)
    )
    source_multiple = bool(ctx.source_field.get("is_multiple_cell_value"))
    result: dict[str, list[Any]] = {}
    for record_id, ids in ids_by_record.items():
        values: list[Any] = []
        for fid in ids:
            if allowed_ids is not None and fid not in allowed_ids:
                continue
            parsed = parse_foreign_value(ctx.source_field, value_map.get(fid))
            if parsed is None:
                continue  # json_agg FILTER (WHERE expr IS NOT NULL)
            if source_multiple and isinstance(parsed, list):
                values.extend(v for v in parsed if v is not None)
            else:
                values.append(parsed)
        result[record_id] = values
    return result


async def _foreign_ids_matching_filter(
    ctx: ComputedContext, rows: list[dict[str, Any]], filter_obj: dict[str, Any]
) -> set[str]:
    """Foreign record ids (among those linked in ``rows``) matching a filter.

    Ports the per-row ``buildForeignFilterSubquery`` predicate by compiling the
    filter against the foreign table's fields with the record filter compiler.
    """
    from .service import RecordService

    link_col = ctx.link_field["db_field_name"]
    linked: set[str] = set()
    for row in rows:
        raw = row.get(link_col)
        cell = json.loads(raw) if isinstance(raw, str) else raw
        linked.update(link_input_ids(cell))
    if not linked:
        return set()
    foreign_fields = await table_repository.list_field_rows(ctx.foreign_table_id)
    svc = RecordService()
    params: dict[str, Any] = {}
    counter = [0]
    compiled = svc._compile_filter(foreign_fields, filter_obj, params, counter)
    where = ' WHERE "__id" = ANY(:__ids)'
    if compiled:
        where += f" AND ({compiled})"
    params["__ids"] = list(linked)
    matched = await repository.list_rows(
        ctx.foreign_base_id, ctx.foreign_table_id, [], where_sql=where, params=params
    )
    return {r["__id"] for r in matched}


async def resolve_lookup(
    field: dict[str, Any], all_fields: list[dict[str, Any]], rows: list[dict[str, Any]]
) -> dict[str, Any]:
    options = json.loads(field["lookup_options"] or "{}")
    ctx = await _load_context(all_fields, options)
    if ctx is None:
        return {}
    allowed = None
    if options.get("filter"):
        allowed = await _foreign_ids_matching_filter(ctx, rows, options["filter"])
    gathered = await gather_link_values(ctx, rows, allowed)
    is_multiple = bool(field.get("is_multiple_cell_value"))
    out: dict[str, Any] = {}
    for record_id, values in gathered.items():
        if is_multiple:
            out[record_id] = values or None
        else:
            out[record_id] = values[0] if values else None
    return out


async def resolve_rollup(
    field: dict[str, Any], all_fields: list[dict[str, Any]], rows: list[dict[str, Any]]
) -> dict[str, Any]:
    if field["type"] == "conditionalRollup":
        return await resolve_conditional_rollup(field, all_fields, rows)
    options = json.loads(field["options"] or "{}")
    lookup_options = json.loads(field["lookup_options"] or "{}")
    ctx = await _load_context(all_fields, lookup_options)
    if ctx is None:
        return {}
    allowed = None
    if lookup_options.get("filter"):
        # conditional rollup: only linked foreign records matching the filter
        # contribute to the aggregation (per-row EXISTS predicate).
        allowed = await _foreign_ids_matching_filter(ctx, rows, lookup_options["filter"])
    gathered = await gather_link_values(ctx, rows, allowed)
    fn = str(options.get("expression", "")).split("(", 1)[0].strip().lower()
    cvt = field["cell_value_type"]
    out: dict[str, Any] = {}
    for record_id, values in gathered.items():
        out[record_id] = _apply_rollup(fn, values, cvt)
    return out


def _clamp_conditional_limit(limit: Any) -> int:
    # ref clampConditionalLimit: truncate to int, drop non-positive, cap at max;
    # a missing/invalid limit falls back to the max (the default query limit).
    if isinstance(limit, bool) or not isinstance(limit, (int, float)):
        return CONDITIONAL_QUERY_MAX_LIMIT
    truncated = int(limit)
    if truncated <= 0:
        return CONDITIONAL_QUERY_MAX_LIMIT
    return min(truncated, CONDITIONAL_QUERY_MAX_LIMIT)


def _is_field_reference(value: Any) -> bool:
    return isinstance(value, dict) and value.get("type") == "field" and "fieldId" in value


def _resolve_reference_value(
    value: Any, row: dict[str, Any], host_by_id: dict[str, dict[str, Any]]
) -> Any:
    # a filter value can compare a foreign field against the *host* record's own
    # field ({type:'field', fieldId}); substitute the host row's live cell value
    # so the foreign query is evaluated per host record.
    from .service import RecordService

    if _is_field_reference(value):
        host_field = host_by_id.get(value["fieldId"])
        if host_field is None:
            return value
        return RecordService._from_db_value(host_field, row.get(host_field["db_field_name"]))
    if isinstance(value, list):
        return [_resolve_reference_value(item, row, host_by_id) for item in value]
    return value


def _resolve_conditional_filter(
    filter_obj: dict[str, Any] | None,
    row: dict[str, Any],
    host_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    if not filter_obj:
        return filter_obj
    resolved_set: list[dict[str, Any]] = []
    for item in filter_obj.get("filterSet") or []:
        if "filterSet" in item:
            resolved_set.append(_resolve_conditional_filter(item, row, host_by_id) or item)
        else:
            new_item = dict(item)
            new_item["value"] = _resolve_reference_value(item.get("value"), row, host_by_id)
            resolved_set.append(new_item)
    return {"conjunction": filter_obj.get("conjunction", "and"), "filterSet": resolved_set}


async def _query_conditional_values(
    foreign_table: dict[str, Any],
    foreign_fields: list[dict[str, Any]],
    source_field: dict[str, Any],
    filter_obj: dict[str, Any] | None,
    sort: dict[str, Any] | None,
    limit: Any,
) -> list[Any]:
    """Ordered, non-null source values of the foreign rows matching ``filter_obj``.

    Ports the reference conditional-rollup foreign query: compile the filter over
    the foreign table, apply the optional sort, cap rows at the configured limit,
    then collect the lookup field's values as the rollup ``{values}`` array.
    """
    from .service import RecordService

    svc = RecordService()
    params: dict[str, Any] = {}
    counter = [0]
    compiled = svc._compile_filter(foreign_fields, filter_obj, params, counter)
    where_sql = (" WHERE " + compiled) if compiled else ""
    order_sql = svc._compile_sort(foreign_fields, [sort] if sort else None)
    matched = await repository.list_rows(
        foreign_table["base_id"],
        foreign_table["id"],
        [source_field["db_field_name"]],
        where_sql=where_sql,
        params=params,
        order_sql=order_sql,
        limit=_clamp_conditional_limit(limit),
    )
    source_multiple = bool(source_field.get("is_multiple_cell_value"))
    column = source_field["db_field_name"]
    values: list[Any] = []
    for r in matched:
        parsed = parse_foreign_value(source_field, r.get(column))
        if parsed is None:
            continue  # json_agg FILTER (WHERE expr IS NOT NULL)
        if source_multiple and isinstance(parsed, list):
            values.extend(v for v in parsed if v is not None)
        else:
            values.append(parsed)
    return values


async def resolve_conditional_rollup(
    field: dict[str, Any], all_fields: list[dict[str, Any]], rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """conditionalRollup: roll up a filtered query over a foreign table.

    Unlike rollup there is no link field — ``options`` carries the foreign table,
    the looked-up field and the filter/sort/limit. The filter may reference the
    host record's own fields, so it is resolved per host row (rows sharing an
    identical resolved filter reuse a single foreign query).
    """
    options = json.loads(field["options"] or "{}")
    foreign_table_id = options.get("foreignTableId")
    lookup_field_id = options.get("lookupFieldId")
    if not foreign_table_id or not lookup_field_id:
        return {}
    foreign_table = await get_table_meta_by_id(foreign_table_id)
    if foreign_table is None:
        return {}
    foreign_fields = await table_repository.list_field_rows(foreign_table_id)
    source_field = next((f for f in foreign_fields if f["id"] == lookup_field_id), None)
    if source_field is None:
        return {}
    filter_obj = options.get("filter")
    sort = options.get("sort")
    limit = options.get("limit")
    fn = str(options.get("expression", "")).split("(", 1)[0].strip().lower()
    cvt = field["cell_value_type"]
    host_by_id = {f["id"]: f for f in all_fields}

    cache: dict[str, Any] = {}
    out: dict[str, Any] = {}
    for row in rows:
        resolved = _resolve_conditional_filter(filter_obj, row, host_by_id)
        signature = json.dumps(resolved, sort_keys=True, default=str)
        if signature not in cache:
            try:
                values = await _query_conditional_values(
                    foreign_table, foreign_fields, source_field, resolved, sort, limit
                )
            except Exception:
                # current-record / dynamic-value filters and operators the scalar
                # compiler cannot express degrade to an empty match rather than
                # failing the whole record read (see docs/audit/99-findings.md).
                values = []
            cache[signature] = _apply_rollup(fn, values, cvt)
        out[row["__id"]] = cache[signature]
    return out



def _num(value: Any) -> Any:
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _apply_rollup(fn: str, values: list[Any], cell_value_type: str) -> Any:
    # values are the ordered, non-null linked source values ({values} array).
    numbers = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if fn == "countall":
        return len(values)
    if fn == "counta":
        return sum(1 for v in values if v not in (None, ""))
    if fn == "count":
        return len(numbers)
    if fn == "sum":
        return _num(sum(numbers)) if values else 0
    if fn == "average":
        return _num(sum(numbers) / len(numbers)) if numbers else 0
    if fn in ("max", "min"):
        if not values:
            return None
        if cell_value_type == "dateTime":
            picked = (max if fn == "max" else min)(values, key=lambda v: str(v))
            return picked
        return _num((max if fn == "max" else min)(numbers)) if numbers else None
    if fn in ("array_join", "concatenate"):
        if not values:
            return None
        return ", ".join(_rollup_str(v) for v in values)
    if fn in ("and", "or", "xor"):
        # logical rollups only apply to boolean source values; over other types
        # (e.g. numeric) the reference yields null.
        if not values or not all(isinstance(v, bool) for v in values):
            return None
        if fn == "and":
            return all(values)
        if fn == "or":
            return any(values)
        return sum(1 for v in values if v) % 2 == 1
    if fn == "array_unique":
        seen: list[Any] = []
        for v in values:
            if v not in seen:
                seen.append(v)
        return seen or None
    if fn == "array_compact":
        return values or None
    return values or None


def _rollup_str(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _formula_timezone(field: dict[str, Any]) -> str:
    options = json.loads(field["options"] or "{}")
    formatting = options.get("formatting")
    if isinstance(formatting, dict) and formatting.get("timeZone"):
        return formatting["timeZone"]
    return options.get("timeZone") or "UTC"


def _formula_eval_order(
    formula_fields: list[dict[str, Any]],
    parsed: dict[str, Any],
    formula_ids: set[str],
) -> list[str]:
    # dependencies first; formula->formula cycles drop the back-edge (the
    # reference tolerates unresolved references rather than erroring).
    order: list[str] = []
    state: dict[str, int] = {}

    def visit(field_id: str) -> None:
        if state.get(field_id, 0):
            return
        state[field_id] = 1
        tree = parsed.get(field_id)
        if tree is not None:
            for ref in reference_field_ids(tree):
                if ref in formula_ids and ref != field_id and state.get(ref, 0) != 1:
                    visit(ref)
        state[field_id] = 2
        order.append(field_id)

    for field in formula_fields:
        visit(field["id"])
    return order


def _formula_record_fields(
    row: dict[str, Any],
    all_fields: list[dict[str, Any]],
    base_resolved: dict[str, dict[str, Any]],
    formula_result: dict[str, dict[str, Any]],
    record_id: str,
    from_db_value: Any,
) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for field in all_fields:
        field_id = field["id"]
        if field_id in formula_result:
            values[field_id] = formula_result[field_id].get(record_id)
        elif field_id in base_resolved:
            values[field_id] = base_resolved[field_id].get(record_id)
        else:
            values[field_id] = from_db_value(field, row.get(field["db_field_name"]))
    return values


async def resolve_formulas(
    all_fields: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    base_resolved: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Per-record formula cell values keyed by ``{fieldId: {recordId: value}}``.

    ``base_resolved`` carries already-computed lookup/rollup values; any
    referenced lookup/rollup field missing from it is resolved here so formulas
    stay correct regardless of projection. Formula-of-formula references are
    evaluated in dependency order.
    """
    formula_fields = [
        f for f in all_fields if f["type"] == "formula" and not f.get("is_lookup")
    ]
    if not formula_fields or not rows:
        return {}
    from .service import RecordService

    from_db_value = RecordService._from_db_value
    resolved = dict(base_resolved or {})
    field_by_id = {f["id"]: f for f in all_fields}
    parsed: dict[str, Any] = {}
    referenced: set[str] = set()
    for field in formula_fields:
        expression = json.loads(field["options"] or "{}").get("expression", "")
        try:
            tree = parse(expression)
        except FormulaError:
            tree = None
        parsed[field["id"]] = tree
        if tree is not None:
            referenced.update(reference_field_ids(tree))

    for ref_id in referenced:
        ref_field = field_by_id.get(ref_id)
        if ref_field is None or ref_id in resolved:
            continue
        if ref_field.get("is_lookup"):
            resolved[ref_id] = await resolve_lookup(ref_field, all_fields, rows)
        elif ref_field["type"] in ("rollup", "conditionalRollup"):
            resolved[ref_id] = await resolve_rollup(ref_field, all_fields, rows)

    formula_ids = {f["id"] for f in formula_fields}
    order = _formula_eval_order(formula_fields, parsed, formula_ids)
    result: dict[str, dict[str, Any]] = {}
    for field_id in order:
        result[field_id] = {}
        tree = parsed.get(field_id)
        if tree is None:
            continue
        timezone = _formula_timezone(field_by_id[field_id])
        for row in rows:
            record_id = row["__id"]
            record_fields = _formula_record_fields(
                row, all_fields, resolved, result, record_id, from_db_value
            )
            try:
                result[field_id][record_id] = to_cell_value(
                    evaluate(
                        tree,
                        field_by_id,
                        record_fields,
                        timezone,
                        record_id,
                        row.get("__auto_number"),
                    )
                )
            except FormulaError:
                result[field_id][record_id] = None
    return result
