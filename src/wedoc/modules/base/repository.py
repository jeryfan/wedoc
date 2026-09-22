"""base persistence.

Raw-row access only — business rules live in service.py. Rows are plain dicts
with snake_case column names.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update

from ...db import engine as db_engine
from ...db.models_meta import Base, UserLastVisit


def _row(instance: Any) -> dict[str, Any]:
    return {c.name: getattr(instance, c.name) for c in instance.__table__.columns}


async def insert_base(fields: dict[str, Any]) -> dict[str, Any]:
    # prisma @updatedAt also stamps the row on INSERT.
    fields = {**fields, "last_modified_time": datetime.now(UTC).replace(tzinfo=None)}
    async with db_engine.session() as session:
        row = (
            (
                await session.execute(
                    Base.__table__.insert().values(**fields).returning(Base.__table__)
                )
            )
            .mappings()
            .first()
        )
        await session.commit()
        return dict(row)


async def get_base_row(base_id: str, include_deleted: bool = False) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        stmt = select(Base).where(Base.id == base_id)
        if not include_deleted:
            stmt = stmt.where(Base.deleted_time.is_(None))
        row = (await session.execute(stmt)).scalars().first()
    return _row(row) if row else None


async def get_max_order(space_id: str) -> float:
    async with db_engine.session() as session:
        value = (
            await session.execute(
                select(func.max(Base.order)).where(
                    Base.space_id == space_id, Base.deleted_time.is_(None)
                )
            )
        ).scalar()
    return float(value) if value is not None else 0.0


async def update_base_row(
    base_id: str, fields: dict[str, Any], touch_last_modified: bool = True
) -> dict[str, Any] | None:
    if touch_last_modified:
        # prisma @updatedAt: every update bumps last_modified_time.
        fields = {**fields, "last_modified_time": datetime.now(UTC).replace(tzinfo=None)}
    async with db_engine.session() as session:
        row = (
            (
                await session.execute(
                    update(Base)
                    .where(Base.id == base_id)
                    .values(**fields)
                    .returning(Base.__table__)
                )
            )
            .mappings()
            .first()
        )
        await session.commit()
        return dict(row) if row else None


async def soft_delete_base_row(base_id: str, deleted_time: datetime) -> None:
    await update_base_row(
        base_id, {"deleted_time": deleted_time, "provision_state": "deleting"}
    )


async def list_base_rows_by_ids(base_ids: list[str]) -> list[dict[str, Any]]:
    if not base_ids:
        return []
    async with db_engine.session() as session:
        rows = (
            (
                await session.execute(
                    select(Base)
                    .where(Base.id.in_(base_ids), Base.deleted_time.is_(None))
                    .order_by(Base.space_id.asc(), Base.order.asc())
                )
            )
            .scalars()
            .all()
        )
    return [_row(r) for r in rows]


async def list_base_rows_by_space_any(
    space_ids: list[str], base_ids: list[str]
) -> list[dict[str, Any]]:
    """getAllBaseList source: visible through base collaborator rows or
    space collaborator rows (with the space still alive)."""
    async with db_engine.session() as session:
        stmt = (
            select(Base)
            .where(Base.deleted_time.is_(None))
            .order_by(Base.space_id.asc(), Base.order.asc())
        )
        conditions = []
        if base_ids:
            conditions.append(Base.id.in_(base_ids))
        if space_ids:
            conditions.append(Base.space_id.in_(space_ids))
        if conditions:
            from sqlalchemy import or_

            stmt = stmt.where(or_(*conditions))
        rows = (await session.execute(stmt)).scalars().all()
    return [_row(r) for r in rows]


async def list_next_base_by_order(
    space_id: str, anchor_order: float, *, below: bool
) -> dict[str, Any] | None:
    """Neighbor of the anchor inside one space, for the order recomputation."""
    async with db_engine.session() as session:
        stmt = select(Base.id, Base.order).where(
            Base.space_id == space_id,
            Base.deleted_time.is_(None),
            Base.order < anchor_order if below else Base.order > anchor_order,
        )
        stmt = stmt.order_by(Base.order.desc() if below else Base.order.asc())
        row = (await session.execute(stmt)).first()
    if row is None:
        return None
    return {"id": row[0], "order": row[1]}


async def mark_base_visited(
    visit_id: str, user_id: str, base_id: str, space_id: str, now: datetime
) -> None:
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    async with db_engine.session() as session:
        stmt = pg_insert(UserLastVisit).values(
            id=visit_id,
            user_id=user_id,
            resource_type="base",
            resource_id=base_id,
            parent_resource_id=space_id,
            last_visit_time=now,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["user_id", "resource_type", "resource_id"],
            set_={"last_visit_time": now},
        )
        await session.execute(stmt)
        await session.commit()
