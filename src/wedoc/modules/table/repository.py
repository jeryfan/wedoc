"""table / field / view meta persistence + physical data table access.

Raw-row access only — business rules live in service.py. Field/view meta rows
live here until the field and view modules absorb them.
"""

from typing import Any

from sqlalchemy import delete, func, select, text, update

from ...db import engine as db_engine
from ...db.models_meta import Field, TableMeta, View


def _row(instance: Any) -> dict[str, Any]:
    return {c.name: getattr(instance, c.name) for c in instance.__table__.columns}


# ---- table meta ---------------------------------------------------------------


async def insert_table_meta(fields: dict[str, Any]) -> dict[str, Any]:
    async with db_engine.session() as session:
        row = (
            (
                await session.execute(
                    TableMeta.__table__.insert().values(**fields).returning(TableMeta.__table__)
                )
            )
            .mappings()
            .first()
        )
        await session.commit()
        return dict(row)


async def get_table_meta_row(
    table_id: str, base_id: str, include_deleted: bool = False
) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        stmt = select(TableMeta).where(TableMeta.id == table_id, TableMeta.base_id == base_id)
        if not include_deleted:
            stmt = stmt.where(TableMeta.deleted_time.is_(None))
        row = (await session.execute(stmt)).scalars().first()
    return _row(row) if row else None


async def list_next_table_by_order(
    base_id: str, anchor_order: float, *, below: bool
) -> dict[str, Any] | None:
    """Neighbor of the anchor inside one base, for the order recomputation."""
    async with db_engine.session() as session:
        stmt = select(TableMeta.id, TableMeta.order).where(
            TableMeta.base_id == base_id,
            TableMeta.deleted_time.is_(None),
            TableMeta.provision_state == "ready",
            TableMeta.order < anchor_order if below else TableMeta.order > anchor_order,
        )
        stmt = stmt.order_by(TableMeta.order.desc() if below else TableMeta.order.asc())
        row = (await session.execute(stmt)).first()
    if row is None:
        return None
    return {"id": row[0], "order": row[1]}


async def soft_delete_table_row(table_id: str, when: Any, version: int) -> None:
    async with db_engine.session() as session:
        await session.execute(
            update(TableMeta)
            .where(TableMeta.id == table_id)
            .values(deleted_time=when, version=version, provision_state="deleting")
        )
        await session.commit()


async def delete_table_cascade_rows(table_id: str) -> None:
    """Permanent delete: meta row, field/view rows and the physical table."""
    async with db_engine.session() as session:
        await session.execute(delete(Field).where(Field.table_id == table_id))
        await session.execute(delete(View).where(View.table_id == table_id))
        await session.execute(delete(TableMeta).where(TableMeta.id == table_id))
        await session.commit()


async def list_table_meta_rows(base_id: str) -> list[dict[str, Any]]:
    async with db_engine.session() as session:
        rows = (
            (
                await session.execute(
                    select(TableMeta)
                    .where(
                        TableMeta.base_id == base_id,
                        TableMeta.deleted_time.is_(None),
                        TableMeta.provision_state == "ready",
                    )
                    .order_by(TableMeta.order.asc())
                )
            )
            .scalars()
            .all()
        )
    return [_row(r) for r in rows]


async def list_table_names_and_orders(base_id: str) -> list[dict[str, Any]]:
    async with db_engine.session() as session:
        rows = (
            (
                await session.execute(
                    select(TableMeta.name, TableMeta.order).where(
                        TableMeta.base_id == base_id, TableMeta.deleted_time.is_(None)
                    )
                )
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


async def find_table_by_db_table_name(db_table_name: str, base_id: str) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        row = (
            (
                await session.execute(
                    select(TableMeta.id).where(
                        TableMeta.db_table_name == db_table_name, TableMeta.base_id == base_id
                    )
                )
            )
            .scalars()
            .first()
        )
    return {"id": row} if row else None


async def update_table_meta_row(table_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        row = (
            (
                await session.execute(
                    update(TableMeta)
                    .where(TableMeta.id == table_id)
                    .values(**fields)
                    .returning(TableMeta.__table__)
                )
            )
            .mappings()
            .first()
        )
        await session.commit()
        return dict(row) if row else None


async def get_default_view_ids(table_ids: list[str]) -> dict[str, str]:
    """First (lowest order) non-deleted view per table."""
    if not table_ids:
        return {}
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(View.table_id, View.id)
                .where(View.table_id.in_(table_ids), View.deleted_time.is_(None))
                .order_by(View.order.asc())
            )
        ).all()
    result: dict[str, str] = {}
    for table_id, view_id in rows:
        result.setdefault(table_id, view_id)
    return result


# ---- field meta -----------------------------------------------------------------


async def insert_field_rows(rows: list[dict[str, Any]]) -> None:
    async with db_engine.session() as session:
        await session.execute(Field.__table__.insert().values(rows))
        await session.commit()


async def list_field_rows(table_id: str) -> list[dict[str, Any]]:
    async with db_engine.session() as session:
        rows = (
            (
                await session.execute(
                    select(Field)
                    .where(Field.table_id == table_id, Field.deleted_time.is_(None))
                    .order_by(Field.order.asc())
                )
            )
            .scalars()
            .all()
        )
    return [_row(r) for r in rows]


# ---- view meta ------------------------------------------------------------------


async def insert_view_row(fields: dict[str, Any]) -> dict[str, Any]:
    async with db_engine.session() as session:
        row = (
            (
                await session.execute(
                    View.__table__.insert().values(**fields).returning(View.__table__)
                )
            )
            .mappings()
            .first()
        )
        await session.commit()
        return dict(row)


async def list_view_rows(table_id: str) -> list[dict[str, Any]]:
    async with db_engine.session() as session:
        rows = (
            (
                await session.execute(
                    select(View)
                    .where(View.table_id == table_id, View.deleted_time.is_(None))
                    .order_by(View.order.asc())
                )
            )
            .scalars()
            .all()
        )
    return [_row(r) for r in rows]


async def copy_data_rows(
    base_id: str, new_table_id: str, old_table_id: str, columns: list[str]
) -> None:
    """INSERT INTO new (cols) SELECT cols FROM old — ids keep v2 semantics."""
    col_list = ", ".join(f'"{c}"' for c in columns)
    sql = (
        f'INSERT INTO "{base_id}"."{new_table_id}" ({col_list}) '
        f'SELECT {col_list} FROM "{base_id}"."{old_table_id}" ORDER BY "__auto_number"'
    )
    async with db_engine.session() as session:
        await session.execute(text(sql))
        await session.commit()


# ---- physical data table --------------------------------------------------------


async def execute_data_ddl(sql_statements: list[str]) -> None:
    async with db_engine.session() as session:
        for sql in sql_statements:
            await session.execute(text(sql))
        await session.commit()


async def insert_data_rows(
    base_id: str, table_id: str, columns: list[str], value_rows: list[list[Any]]
) -> None:
    """INSERT INTO "base"."table" (cols...) VALUES ... — identifiers are generated."""
    if not value_rows:
        return
    quoted_cols = ", ".join(f'"{c}"' for c in columns)
    tuples = []
    params: dict[str, Any] = {}
    for i, row in enumerate(value_rows):
        names = [f"p{i}_{j}" for j in range(len(columns))]
        tuples.append(f"({', '.join(':' + n for n in names)})")
        for name, value in zip(names, row, strict=True):
            params[name] = value
    sql = f'INSERT INTO "{base_id}"."{table_id}" ({quoted_cols}) VALUES {", ".join(tuples)}'
    async with db_engine.session() as session:
        await session.execute(text(sql), params)
        await session.commit()


async def fetch_data_row_meta(
    base_id: str, table_id: str, record_ids: list[str]
) -> dict[str, dict[str, Any]]:
    """System-column metadata keyed by ``__id`` for freshly inserted rows."""
    if not record_ids:
        return {}
    sql = (
        'SELECT "__id", "__auto_number", "__created_time", "__last_modified_time", '
        '"__created_by", "__last_modified_by" '
        f'FROM "{base_id}"."{table_id}" WHERE "__id" = ANY(:ids)'
    )
    async with db_engine.session() as session:
        rows = (await session.execute(text(sql), {"ids": record_ids})).mappings().all()
    return {r["__id"]: dict(r) for r in rows}


async def max_field_order(table_id: str) -> float:
    async with db_engine.session() as session:
        value = (
            await session.execute(
                select(func.max(Field.order)).where(
                    Field.table_id == table_id, Field.deleted_time.is_(None)
                )
            )
        ).scalar()
    return float(value) if value is not None else 0.0
