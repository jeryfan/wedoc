"""record persistence: raw rows of the per-table physical data table +
record_history rows and user lookups in the meta schema."""

from typing import Any

from sqlalchemy import select, text

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
