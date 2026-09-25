"""field meta persistence + physical data table access.

Raw-row access only — business rules live in service.py. Shared helpers
(insert_field_rows / list_field_rows / execute_data_ddl) stay in the table
repository until the absorption refactor.
"""

from typing import Any

from sqlalchemy import select, text, update

from ...core.ids import IdPrefix, new_id
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


async def list_fields_referencing_foreign_table(
    foreign_table_id: str,
) -> list[dict[str, Any]]:
    """Live lookup/rollup fields whose link resolves to ``foreign_table_id``.

    Used to recompute dependent computed columns when the foreign (source) table
    changes. The LIKE prefilter narrows to rows whose ``lookupOptions`` mentions
    the id; the JSON parse confirms it is actually the ``foreignTableId``.
    """
    import json

    async with db_engine.session() as session:
        stmt = select(Field).where(
            Field.deleted_time.is_(None),
            Field.lookup_options.is_not(None),
            Field.lookup_options.contains(foreign_table_id),
        )
        rows = (await session.execute(stmt)).scalars().all()
    result: list[dict[str, Any]] = []
    for row in rows:
        try:
            options = json.loads(row.lookup_options or "{}")
        except (TypeError, ValueError):
            continue
        if options.get("foreignTableId") != foreign_table_id:
            continue
        if row.is_lookup or row.type in ("rollup", "conditionalRollup"):
            result.append(_row(row))
    return result


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


async def count_non_null(base_id: str, table_id: str, db_field_name: str) -> int:
    async with db_engine.session() as session:
        value = await session.execute(
            text(
                f'SELECT count(*) FROM "{base_id}"."{table_id}" '
                f'WHERE "{db_field_name}" IS NOT NULL'
            )
        )
    return int(value.scalar() or 0)


async def list_cell_values(
    base_id: str, table_id: str, db_field_name: str
) -> list[tuple[str, Any]]:
    """``(record id, raw column value)`` for every row, in insertion order."""
    sql = (
        f'SELECT "__id" AS id, "{db_field_name}" AS value '
        f'FROM "{base_id}"."{table_id}" ORDER BY "__auto_number"'
    )
    async with db_engine.session() as session:
        rows = (await session.execute(text(sql))).mappings().all()
    return [(r["id"], r["value"]) for r in rows]


async def replace_field_column(
    base_id: str,
    table_id: str,
    old_db_field_name: str,
    new_db_field_name: str,
    column_type: str,
    cell_values: list[tuple[str, Any]],
) -> None:
    """Retype a physical column by writing already-cast values into a fresh
    column and swapping it in, rather than an in-place ``ALTER ... USING`` cast
    that fails or corrupts data on incompatible pairs. One transaction so the
    old column survives if any step fails."""
    table = f'"{base_id}"."{table_id}"'
    temp = f"cvt_{new_id(IdPrefix.FIELD, 16)}"
    async with db_engine.session() as session:
        await session.execute(
            text(f'ALTER TABLE {table} ADD COLUMN "{temp}" {column_type} NULL')
        )
        for record_id, value in cell_values:
            await session.execute(
                text(f'UPDATE {table} SET "{temp}" = :value WHERE "__id" = :rid'),
                {"value": value, "rid": record_id},
            )
        await session.execute(
            text(f'ALTER TABLE {table} DROP COLUMN "{old_db_field_name}"')
        )
        await session.execute(
            text(f'ALTER TABLE {table} RENAME COLUMN "{temp}" TO "{new_db_field_name}"')
        )
        await session.commit()


async def rename_field_column(
    base_id: str, table_id: str, old_db_field_name: str, new_db_field_name: str
) -> None:
    async with db_engine.session() as session:
        await session.execute(
            text(
                f'ALTER TABLE "{base_id}"."{table_id}" '
                f'RENAME COLUMN "{old_db_field_name}" TO "{new_db_field_name}"'
            )
        )
        await session.commit()
