"""record persistence: raw rows of the per-table physical data table."""

from typing import Any

from sqlalchemy import text

from ...db import engine as db_engine

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
    sql = (
        f"UPDATE {_q(base_id, table_id)} SET {sets}, "
        '"__version" = "__version" + 1, "__last_modified_time" = now() '
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
