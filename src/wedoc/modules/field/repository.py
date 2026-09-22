"""field meta persistence + physical data table access.

Raw-row access only — business rules live in service.py. Shared helpers
(insert_field_rows / list_field_rows / execute_data_ddl) stay in the table
repository until the absorption refactor.
"""

from typing import Any

from sqlalchemy import select, text, update

from ...db import engine as db_engine
from ...db.models_meta import Field, TableMeta


def _row(instance: Any) -> dict[str, Any]:
    return {c.name: getattr(instance, c.name) for c in instance.__table__.columns}


async def get_table_meta_by_id(
    table_id: str, include_deleted: bool = False
) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        stmt = select(TableMeta).where(TableMeta.id == table_id)
        if not include_deleted:
            stmt = stmt.where(TableMeta.deleted_time.is_(None))
        row = (await session.execute(stmt)).scalars().first()
    return _row(row) if row else None


async def get_field_row(
    table_id: str, field_id: str, include_deleted: bool = False
) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        stmt = select(Field).where(Field.id == field_id, Field.table_id == table_id)
        if not include_deleted:
            stmt = stmt.where(Field.deleted_time.is_(None))
        row = (await session.execute(stmt)).scalars().first()
    return _row(row) if row else None


async def update_field_row(field_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        row = (
            (
                await session.execute(
                    update(Field)
                    .where(Field.id == field_id)
                    .values(**fields)
                    .returning(Field.__table__)
                )
            )
            .mappings()
            .first()
        )
        await session.commit()
        return dict(row) if row else None


async def soft_delete_field_row(field_id: str, when: Any, version: int) -> None:
    async with db_engine.session() as session:
        await session.execute(
            update(Field).where(Field.id == field_id).values(deleted_time=when, version=version)
        )
        await session.commit()


async def count_data_rows(base_id: str, table_id: str) -> int:
    async with db_engine.session() as session:
        value = await session.execute(text(f'SELECT count(*) FROM "{base_id}"."{table_id}"'))
    return int(value.scalar() or 0)
