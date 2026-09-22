"""view meta persistence.

Raw-row access only — business rules live in service.py. insert_view_row /
list_view_rows stay in the table repository until the absorption refactor.
"""

from typing import Any

from sqlalchemy import func, select, text, update

from ...db import engine as db_engine
from ...db.models_meta import View


def _row(instance: Any) -> dict[str, Any]:
    return {c.name: getattr(instance, c.name) for c in instance.__table__.columns}


async def get_view_row(
    table_id: str, view_id: str, include_deleted: bool = False
) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        stmt = select(View).where(View.id == view_id, View.table_id == table_id)
        if not include_deleted:
            stmt = stmt.where(View.deleted_time.is_(None))
        row = (await session.execute(stmt)).scalars().first()
    return _row(row) if row else None


async def update_view_row(view_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        row = (
            (
                await session.execute(
                    update(View)
                    .where(View.id == view_id)
                    .values(**fields)
                    .returning(View.__table__)
                )
            )
            .mappings()
            .first()
        )
        await session.commit()
        return dict(row) if row else None


async def soft_delete_view_row(view_id: str, when: Any, version: int) -> None:
    async with db_engine.session() as session:
        await session.execute(
            update(View).where(View.id == view_id).values(deleted_time=when, version=version)
        )
        await session.commit()


async def max_view_order(table_id: str) -> float:
    async with db_engine.session() as session:
        value = (
            await session.execute(
                select(func.max(View.order)).where(
                    View.table_id == table_id, View.deleted_time.is_(None)
                )
            )
        ).scalar()
    return float(value) if value is not None else 0.0


async def list_next_view_by_order(
    table_id: str, anchor_order: float, *, below: bool
) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        stmt = select(View.id, View.order).where(
            View.table_id == table_id,
            View.deleted_time.is_(None),
            View.order < anchor_order if below else View.order > anchor_order,
        )
        stmt = stmt.order_by(View.order.desc() if below else View.order.asc())
        row = (await session.execute(stmt)).first()
    if row is None:
        return None
    return {"id": row[0], "order": row[1]}


async def record_exists(base_id: str, table_id: str, record_id: str) -> bool:
    async with db_engine.session() as session:
        value = await session.execute(
            text(f'SELECT 1 FROM "{base_id}"."{table_id}" WHERE "__id" = :rid'),
            {"rid": record_id},
        )
    return value.first() is not None
