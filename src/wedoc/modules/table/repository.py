"""table / field / view meta persistence + physical data table access.

Raw-row access only — business rules live in service.py. Field/view meta rows
live here until the field and view modules absorb them.
"""

from typing import Any

from sqlalchemy import func, select, text, update

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


async def get_table_meta_row(table_id: str, base_id: str) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        row = (
            (
                await session.execute(
                    select(TableMeta).where(
                        TableMeta.id == table_id,
                        TableMeta.base_id == base_id,
                        TableMeta.deleted_time.is_(None),
                    )
                )
            )
            .scalars()
            .first()
        )
    return _row(row) if row else None


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
