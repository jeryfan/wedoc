"""record persistence: raw rows of the per-table physical data table +
record_history rows and user lookups in the meta schema."""

from typing import Any

from sqlalchemy import select, text

from ...core import cls
from ...db import engine as db_engine
from ...db.models_meta import User

SYSTEM_COLUMNS = (
    "__id",
    "__auto_number",
    "__created_time",
    "__last_modified_time",
    "__created_by",
    "__last_modified_by",
    "__version",
)


def _q(base_id: str, table_id: str) -> str:
    return f'"{base_id}"."{table_id}"'


async def fetch_row(
    base_id: str, table_id: str, record_id: str, field_columns: list[str]
) -> dict[str, Any] | None:
    cols = ", ".join(f'"{c}"' for c in SYSTEM_COLUMNS + tuple(field_columns))
    async with db_engine.session() as session:
        result = await session.execute(
            text(f"SELECT {cols} FROM {_q(base_id, table_id)} WHERE __id = :rid"),
            {"rid": record_id},
        )
        row = result.mappings().first()
    return dict(row) if row else None


async def list_rows(
    base_id: str,
    table_id: str,
    field_columns: list[str],
    where_sql: str = "",
    params: dict[str, Any] | None = None,
    order_sql: str = "",
    limit: int | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    cols = ", ".join(f'"{c}"' for c in SYSTEM_COLUMNS + tuple(field_columns))
    sql = f"SELECT {cols} FROM {_q(base_id, table_id)}{where_sql}{order_sql}"
    if limit is not None:
        sql += " LIMIT :limit OFFSET :offset"
    bind = dict(params or {})
    if limit is not None:
        bind["limit"] = limit
        bind["offset"] = offset
    async with db_engine.session() as session:
        rows = (await session.execute(text(sql), bind)).mappings().all()
    return [dict(r) for r in rows]


async def count_rows(
    base_id: str, table_id: str, where_sql: str = "", params: dict[str, Any] | None = None
) -> int:
    sql = f"SELECT count(*) FROM {_q(base_id, table_id)}{where_sql}"
    async with db_engine.session() as session:
        value = await session.execute(text(sql), params or {})
    return int(value.scalar() or 0)


async def distinct_column_values(
    base_id: str, table_id: str, column: str
) -> list[Any]:
    """Distinct non-null values of a physical column (for record collaborators)."""
    sql = f'SELECT DISTINCT "{column}" FROM {_q(base_id, table_id)} WHERE "{column}" IS NOT NULL'
    async with db_engine.session() as session:
        rows = (await session.execute(text(sql))).all()
    return [r[0] for r in rows]


async def fetch_column_by_ids(
    base_id: str, table_id: str, column: str, ids: list[str]
) -> dict[str, Any]:
    """Map ``__id -> column`` for the given record ids (computed-field resolution)."""
    if not ids:
        return {}
    sql = (
        f'SELECT "__id" AS id, "{column}" AS value FROM {_q(base_id, table_id)} '
        'WHERE "__id" = ANY(:ids)'
    )
    async with db_engine.session() as session:
        rows = (await session.execute(text(sql), {"ids": ids})).mappings().all()
    return {r["id"]: r["value"] for r in rows}


async def insert_row(base_id: str, table_id: str, values: dict[str, Any]) -> None:
    cols = ", ".join(f'"{c}"' for c in values)
    names = ", ".join(f":v{i}" for i in range(len(values)))
    sql = f"INSERT INTO {_q(base_id, table_id)} ({cols}) VALUES ({names})"
    async with db_engine.session() as session:
        await session.execute(text(sql), {f"v{i}": v for i, v in enumerate(values.values())})
        await session.commit()


async def update_row(base_id: str, table_id: str, record_id: str, values: dict[str, Any]) -> None:
    sets = ", ".join(f'"{c}" = :s{i}' for i, c in enumerate(values))
    bind = {f"s{i}": v for i, v in enumerate(values.values())}
    bind["rid"] = record_id
    bind["lmb"] = cls.get("user.id")
    sql = (
        f"UPDATE {_q(base_id, table_id)} SET {sets}, "
        '"__version" = "__version" + 1, "__last_modified_time" = now(), '
        '"__last_modified_by" = :lmb '
        "WHERE __id = :rid"
    )
    async with db_engine.session() as session:
        await session.execute(text(sql), bind)
        await session.commit()


async def delete_row(base_id: str, table_id: str, record_id: str) -> None:
    async with db_engine.session() as session:
        await session.execute(
            text(f"DELETE FROM {_q(base_id, table_id)} WHERE __id = :rid"),
            {"rid": record_id},
        )
        await session.commit()


async def column_exists(base_id: str, table_id: str, column: str) -> bool:
    sql = (
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = :s AND table_name = :t AND column_name = :c"
    )
    async with db_engine.session() as session:
        value = await session.execute(
            text(sql), {"s": base_id, "t": table_id, "c": column}
        )
    return value.first() is not None


async def ensure_view_order_column(base_id: str, table_id: str, column: str) -> None:
    """Lazily create a per-view row-order column, seeded from ``__auto_number``.

    Mirrors the reference row-index column (``__row_{viewId}``): a view without
    it orders by ``__auto_number``; once manual reorder happens the column is
    created and becomes the view's order.
    """
    if await column_exists(base_id, table_id, column):
        return
    table = _q(base_id, table_id)
    index_name = f"idx_{column}"[:63]
    async with db_engine.session() as session:
        await session.execute(
            text(f'ALTER TABLE {table} ADD COLUMN "{column}" double precision')
        )
        await session.execute(text(f'UPDATE {table} SET "{column}" = "__auto_number"'))
        await session.execute(
            text(f'CREATE INDEX IF NOT EXISTS "{index_name}" ON {table} ("{column}")')
        )
        await session.commit()


async def get_column_value(base_id: str, table_id: str, column: str, record_id: str) -> Any:
    sql = f'SELECT "{column}" AS v FROM {_q(base_id, table_id)} WHERE "__id" = :rid'
    async with db_engine.session() as session:
        value = await session.execute(text(sql), {"rid": record_id})
    row = value.first()
    return row[0] if row else None


async def neighbor_order_value(
    base_id: str, table_id: str, column: str, anchor_value: float, below: bool, exclude: list[str]
) -> float | None:
    """Order value of the row immediately beyond ``anchor_value`` on one side,
    skipping the records being moved (``exclude``)."""
    op = ">" if below else "<"
    direction = "ASC" if below else "DESC"
    sql = (
        f'SELECT "{column}" AS v FROM {_q(base_id, table_id)} '
        f'WHERE "{column}" {op} :anchor AND NOT ("__id" = ANY(:exclude)) '
        f'ORDER BY "{column}" {direction} LIMIT 1'
    )
    async with db_engine.session() as session:
        value = await session.execute(
            text(sql), {"anchor": anchor_value, "exclude": exclude or [""]}
        )
    row = value.first()
    return row[0] if row else None


async def set_column_values(
    base_id: str, table_id: str, column: str, values: dict[str, float]
) -> None:
    if not values:
        return
    sql = f'UPDATE {_q(base_id, table_id)} SET "{column}" = :val WHERE "__id" = :rid'
    async with db_engine.session() as session:
        for record_id, order in values.items():
            await session.execute(text(sql), {"val": order, "rid": record_id})
        await session.commit()


async def set_computed_columns(
    base_id: str, table_id: str, record_id: str, values: dict[str, Any]
) -> None:
    """Write materialized computed (formula/lookup/rollup) columns for a record.

    Unlike ``update_row`` this touches no system columns: the value is derived,
    not a user edit, so it must not bump ``__version``/``__last_modified_*``.
    """
    if not values:
        return
    sets = ", ".join(f'"{c}" = :s{i}' for i, c in enumerate(values))
    bind: dict[str, Any] = {f"s{i}": v for i, v in enumerate(values.values())}
    bind["rid"] = record_id
    sql = f"UPDATE {_q(base_id, table_id)} SET {sets} WHERE __id = :rid"
    async with db_engine.session() as session:
        await session.execute(text(sql), bind)
        await session.commit()


async def list_ids_linking_to(
    base_id: str, table_id: str, link_column: str, foreign_ids: list[str], is_multiple: bool
) -> list[str]:
    """Record ids whose denormalized link cell references any of ``foreign_ids``.

    Reads the same ``{id,title}`` / ``[{id,title}]`` cell the read path aggregates
    from, so dependent lookup/rollup recomputation stays consistent with reads.
    """
    if not foreign_ids:
        return []
    col = f'"{link_column}"'
    if is_multiple:
        predicate = (
            f"jsonb_exists_any(jsonb_path_query_array({col}::jsonb, '$[*].id'), :fids)"
        )
    else:
        predicate = f"({col}::jsonb ->> 'id') = ANY(:fids)"
    sql = (
        f'SELECT "__id" AS id FROM {_q(base_id, table_id)} '
        f"WHERE {col} IS NOT NULL AND {predicate}"
    )
    async with db_engine.session() as session:
        rows = (await session.execute(text(sql), {"fids": list(foreign_ids)})).mappings().all()
    return [r["id"] for r in rows]


# ---- record history (meta schema) --------------------------------------------


async def insert_history(rows: list[dict[str, Any]]) -> None:
    # one commit per row: created_time comes from clock_timestamp() so rows
    # written in the same request keep distinct millisecond timestamps, which
    # is what the DESC ordering observed on the reference implementation
    # relies on.
    sql = (
        'INSERT INTO "record_history" ("id", "table_id", "record_id", "field_id", '
        '"before", "after", "created_time", "created_by") VALUES '
        "(:id, :table_id, :record_id, :field_id, :before, :after, clock_timestamp(), :created_by)"
    )
    for row in rows:
        async with db_engine.session() as session:
            await session.execute(text(sql), row)
            await session.commit()


async def list_history(
    table_id: str,
    record_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    field_ids: list[str] | None = None,
    created_by_ids: list[str] | None = None,
    cursor_time: str | None = None,
    cursor_id: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    clauses = ['"table_id" = :table_id']
    params: dict[str, Any] = {"table_id": table_id}
    if record_id:
        clauses.append('"record_id" = :record_id')
        params["record_id"] = record_id
    if start_date:
        clauses.append('"created_time" >= CAST(:start_date AS timestamp)')
        params["start_date"] = start_date
    if end_date:
        clauses.append('"created_time" <= CAST(:end_date AS timestamp)')
        params["end_date"] = end_date
    if field_ids:
        clauses.append('"field_id" = ANY(:field_ids)')
        params["field_ids"] = field_ids
    if created_by_ids:
        clauses.append('"created_by" = ANY(:created_by_ids)')
        params["created_by_ids"] = created_by_ids
    if cursor_time and cursor_id:
        clauses.append(
            '("created_time" < CAST(:cursor_time AS timestamp) OR '
            '("created_time" = CAST(:cursor_time AS timestamp) AND "id" < :cursor_id))'
        )
        params["cursor_time"] = cursor_time
        params["cursor_id"] = cursor_id
    sql = (
        f'SELECT * FROM "record_history" WHERE {" AND ".join(clauses)} '
        'ORDER BY "created_time" DESC, "id" DESC LIMIT :limit'
    )
    params["limit"] = limit
    async with db_engine.session() as session:
        rows = (await session.execute(text(sql), params)).mappings().all()
    return [dict(r) for r in rows]


async def get_users_by_ids(ids: list[str]) -> list[dict[str, Any]]:
    async with db_engine.session() as session:
        rows = (
            (
                await session.execute(
                    select(
                        User.id,
                        User.name,
                        User.email,
                        User.avatar,
                    ).where(User.id.in_(ids))
                )
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]
