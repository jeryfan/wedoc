"""Link cell read/write engine.

The denormalized ``{id,title}`` / ``[{id,title}]`` cell lives in the field's
base JSONB column (what record reads return). The relational source of truth is
maintained in parallel: the foreign-key column on the host row (ManyOne/OneOne),
or a junction table / foreign row (ManyMany/OneMany). Titles are resolved from
the foreign table's ``lookupFieldId`` at write time (ports the synchronous part
of calculation/link.service). Symmetric (foreign-side) denormalized cells are
recomputed from the foreign keys after the write.
"""

import json
from typing import Any

from sqlalchemy import text

from ...db import engine as db_engine
from ...db.provider import parse_db_table_name


def link_input_ids(value: Any) -> list[str]:
    """Extract record ids from a link cell input ({id}/[{id}]/null)."""
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    ids: list[str] = []
    for item in items:
        if isinstance(item, dict) and item.get("id"):
            ids.append(item["id"])
        elif isinstance(item, str):
            ids.append(item)
    return ids


def _cell(id_: str, title: str | None) -> dict[str, Any]:
    return {"id": id_, "title": title} if title else {"id": id_}


class LinkFieldContext:
    """Foreign-table lookup metadata for one link field."""

    def __init__(
        self,
        field: dict[str, Any],
        options: dict[str, Any],
        foreign_table: dict[str, Any],
        foreign_primary: dict[str, Any],
    ) -> None:
        self.field = field
        self.options = options
        self.is_multiple = bool(field.get("is_multiple_cell_value"))
        self.relationship = options["relationship"]
        self.foreign_table = foreign_table
        self.foreign_base_id = foreign_table["base_id"]
        self.foreign_db_table = foreign_table["db_table_name"]
        self.foreign_primary_col = foreign_primary["db_field_name"]
        self.foreign_primary_type = foreign_primary["type"]
        self.sym_field: dict[str, Any] | None = None

    async def resolve_titles(self, ids: list[str]) -> dict[str, str | None]:
        if not ids:
            return {}
        schema, table = parse_db_table_name(self.foreign_db_table)
        sql = text(
            f'SELECT "__id" AS id, "{self.foreign_primary_col}" AS title '
            f'FROM "{schema}"."{table}" WHERE "__id" = ANY(:ids)'
        )
        async with db_engine.session() as session:
            rows = (await session.execute(sql, {"ids": ids})).mappings().all()
        titles: dict[str, str | None] = {}
        for row in rows:
            raw = row["title"]
            titles[row["id"]] = None if raw is None or raw == "" else _title_to_str(raw)
        return titles


def _title_to_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        return str(value)
    if isinstance(value, (dict, list)):
        # structured foreign primary (e.g. user/link title); best-effort string.
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


async def resolve_link_cell(ctx: LinkFieldContext, value: Any) -> Any:
    """Denormalized cell value ({id,title}/[{id,title}]/None) for the base column."""
    ids = link_input_ids(value)
    if not ids:
        return None
    titles = await ctx.resolve_titles(ids)
    cells = [_cell(i, titles.get(i)) for i in ids]
    return cells if ctx.is_multiple else cells[0]


def own_row_fk_values(ctx: LinkFieldContext, ids: list[str]) -> dict[str, Any]:
    """Foreign-key columns stored on the host row (ManyOne/OneOne only)."""
    if ctx.relationship not in ("manyOne", "oneOne"):
        return {}
    fk = ctx.options["foreignKeyName"]
    if not ids:
        return {fk: None, f"{fk}_order": None}
    return {fk: ids[0], f"{fk}_order": 1.0}


async def sync_relation(
    ctx: LinkFieldContext, self_base_id: str, self_db_table: str, self_id: str, ids: list[str]
) -> set[str]:
    """Persist the foreign keys for a link write; return foreign ids to refresh.

    ManyOne/OneOne keep the FK on the host row (already written with the row),
    so this only clears/records the previously-linked ids for symmetric refresh.
    ManyMany rewrites the junction rows; OneMany rewrites the foreign self-key.
    """
    self_schema, self_table = parse_db_table_name(self_db_table)
    fk_schema, fk_table = parse_db_table_name(ctx.options["fkHostTableName"])
    self_key = ctx.options["selfKeyName"]
    foreign_key = ctx.options["foreignKeyName"]
    affected: set[str] = set(ids)

    async with db_engine.session() as session:
        if ctx.relationship in ("manyOne", "oneOne"):
            prev = (
                await session.execute(
                    text(
                        f'SELECT "{foreign_key}" AS fid FROM "{self_schema}"."{self_table}" '
                        'WHERE "__id" = :sid'
                    ),
                    {"sid": self_id},
                )
            ).scalar()
            if prev:
                affected.add(prev)
            return affected

        if ctx.relationship == "manyMany" or (
            ctx.relationship == "oneMany" and bool(ctx.options.get("isOneWay"))
        ):
            prev_rows = (
                await session.execute(
                    text(
                        f'SELECT "{foreign_key}" AS fid FROM "{fk_schema}"."{fk_table}" '
                        f'WHERE "{self_key}" = :sid'
                    ),
                    {"sid": self_id},
                )
            ).scalars().all()
            affected.update(v for v in prev_rows if v)
            await session.execute(
                text(f'DELETE FROM "{fk_schema}"."{fk_table}" WHERE "{self_key}" = :sid'),
                {"sid": self_id},
            )
            has_order = ctx.relationship == "manyMany"
            for order, fid in enumerate(ids, start=1):
                cols = f'"{self_key}", "{foreign_key}"'
                vals = ":sid, :fid"
                params: dict[str, Any] = {"sid": self_id, "fid": fid}
                if has_order:
                    cols += ', "__order"'
                    vals += ", :ord"
                    params["ord"] = float(order)
                await session.execute(
                    text(
                        f'INSERT INTO "{fk_schema}"."{fk_table}" ({cols}) VALUES ({vals})'
                    ),
                    params,
                )
            await session.commit()
            return affected

        if ctx.relationship == "oneMany":
            # two-way: self-key lives on the foreign (many) row, pointing back here.
            order_col = f"{self_key}_order"
            prev_rows = (
                await session.execute(
                    text(
                        f'SELECT "__id" AS fid FROM "{fk_schema}"."{fk_table}" '
                        f'WHERE "{self_key}" = :sid'
                    ),
                    {"sid": self_id},
                )
            ).scalars().all()
            affected.update(v for v in prev_rows if v)
            await session.execute(
                text(
                    f'UPDATE "{fk_schema}"."{fk_table}" '
                    f'SET "{self_key}" = NULL, "{order_col}" = NULL WHERE "{self_key}" = :sid'
                ),
                {"sid": self_id},
            )
            for order, fid in enumerate(ids, start=1):
                await session.execute(
                    text(
                        f'UPDATE "{fk_schema}"."{fk_table}" '
                        f'SET "{self_key}" = :sid, "{order_col}" = :ord WHERE "__id" = :fid'
                    ),
                    {"sid": self_id, "ord": float(order), "fid": fid},
                )
            await session.commit()
            return affected

    return affected


async def refresh_symmetric_cells(
    ctx: LinkFieldContext,
    self_db_table: str,
    self_primary_col: str,
    foreign_ids: set[str],
    sym_field: dict[str, Any] | None,
) -> None:
    """Recompute the foreign-side denormalized cells from the foreign keys."""
    if not foreign_ids or sym_field is None:
        return
    self_schema, self_table = parse_db_table_name(self_db_table)
    fk_schema, fk_table = parse_db_table_name(ctx.options["fkHostTableName"])
    foreign_schema, foreign_table = parse_db_table_name(ctx.foreign_db_table)
    self_key = ctx.options["selfKeyName"]
    foreign_key = ctx.options["foreignKeyName"]
    sym_col = sym_field["db_field_name"]
    sym_multiple = bool(sym_field.get("is_multiple_cell_value"))
    relationship = ctx.relationship

    async with db_engine.session() as session:
        for fid in foreign_ids:
            if relationship in ("manyOne", "oneOne"):
                sql = (
                    f'SELECT s."__id" AS id, s."{self_primary_col}" AS title '
                    f'FROM "{self_schema}"."{self_table}" s '
                    f'WHERE s."{foreign_key}" = :fid '
                    f'ORDER BY s."{foreign_key}_order" NULLS LAST, s."__auto_number"'
                )
            elif relationship == "manyMany" or (
                relationship == "oneMany" and bool(ctx.options.get("isOneWay"))
            ):
                order_by = ' j."__order"' if relationship == "manyMany" else ' j."__id"'
                sql = (
                    f'SELECT s."__id" AS id, s."{self_primary_col}" AS title '
                    f'FROM "{fk_schema}"."{fk_table}" j '
                    f'JOIN "{self_schema}"."{self_table}" s ON s."__id" = j."{self_key}" '
                    f'WHERE j."{foreign_key}" = :fid ORDER BY{order_by}'
                )
            else:  # oneMany two-way: self-key on the foreign row
                sql = (
                    f'SELECT s."__id" AS id, s."{self_primary_col}" AS title '
                    f'FROM "{self_schema}"."{self_table}" s '
                    f'WHERE s."__id" = (SELECT "{self_key}" '
                    f'FROM "{foreign_schema}"."{foreign_table}" WHERE "__id" = :fid) '
                    'ORDER BY s."__auto_number"'
                )
            rows = (await session.execute(text(sql), {"fid": fid})).mappings().all()
            cells = [_cell(r["id"], _title_to_str(r["title"])) for r in rows]
            if sym_multiple:
                cell_value: Any = cells or None
            else:
                cell_value = cells[0] if cells else None
            await session.execute(
                text(
                    f'UPDATE "{foreign_schema}"."{foreign_table}" SET "{sym_col}" = :val '
                    'WHERE "__id" = :fid'
                ),
                {"val": json.dumps(cell_value) if cell_value is not None else None, "fid": fid},
            )
        await session.commit()
