"""Pin persistence + resource fetchers — raw-row access for the pin service."""

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from ...core.errors import ApiError, HttpErrorCode
from ...core.ids import cuid
from ...db import engine as db_engine
from ...db.models_meta import (
    Base,
    BaseNode,
    Dashboard,
    PinResource,
    Space,
    TableMeta,
    UserLastVisit,
    View,
)

_ALREADY_EXISTS = {"localization": {"i18nKey": "httpErrors.pin.alreadyExists"}}
_NOT_FOUND = {"localization": {"i18nKey": "httpErrors.pin.notFound"}}
_ANCHOR_NOT_FOUND = {"localization": {"i18nKey": "httpErrors.pin.anchorNotFound"}}


async def get_max_order(user_id: str) -> float:
    async with db_engine.session() as session:
        value = (
            await session.execute(
                select(func.max(PinResource.order)).where(PinResource.created_by == user_id)
            )
        ).scalar()
    return value or 0


def _pin_row(mapping: Any) -> dict[str, Any]:
    return {
        "id": mapping["id"],
        "type": mapping["type"],
        "resourceId": mapping["resource_id"],
        "createdTime": mapping["created_time"],
        "createdBy": mapping["created_by"],
        "order": mapping["order"],
    }


async def add_pin(
    user_id: str, pin_type: str, resource_id: str, order: float
) -> dict[str, Any]:
    pin_id = cuid()
    async with db_engine.session() as session:
        try:
            await session.execute(
                PinResource.__table__.insert().values(
                    id=pin_id,
                    type=pin_type,
                    resource_id=resource_id,
                    created_by=user_id,
                    order=order,
                )
            )
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise ApiError(
                "Pin already exists", HttpErrorCode.VALIDATION_ERROR, _ALREADY_EXISTS
            ) from exc
        row = (
            await session.execute(
                select(
                    PinResource.id,
                    PinResource.type,
                    PinResource.resource_id,
                    PinResource.created_time,
                    PinResource.created_by,
                    PinResource.order,
                ).where(PinResource.id == pin_id)
            )
        ).mappings().one()
    return _pin_row(row)


async def delete_pin(user_id: str, pin_type: str, resource_id: str) -> dict[str, Any]:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(
                    PinResource.id,
                    PinResource.type,
                    PinResource.resource_id,
                    PinResource.created_time,
                    PinResource.created_by,
                    PinResource.order,
                ).where(
                    PinResource.created_by == user_id,
                    PinResource.resource_id == resource_id,
                    PinResource.type == pin_type,
                )
            )
        ).mappings().one_or_none()
        if row is None:
            raise ApiError("Pin not found", HttpErrorCode.NOT_FOUND, _NOT_FOUND)
        result = _pin_row(row)
        await session.execute(
            PinResource.__table__.delete().where(
                PinResource.created_by == user_id,
                PinResource.resource_id == resource_id,
                PinResource.type == pin_type,
            )
        )
        await session.commit()
    return result


async def list_pins(user_id: str) -> list[dict[str, Any]]:
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(PinResource.resource_id, PinResource.type, PinResource.order)
                .where(PinResource.created_by == user_id)
                .order_by(PinResource.order.asc())
            )
        ).all()
    return [{"resourceId": r[0], "type": r[1], "order": r[2]} for r in rows]


async def find_pin(user_id: str, pin_type: str, resource_id: str) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(PinResource.id, PinResource.order).where(
                    PinResource.resource_id == resource_id,
                    PinResource.type == pin_type,
                    PinResource.created_by == user_id,
                )
            )
        ).first()
    return {"id": row[0], "order": row[1]} if row else None


async def get_next_pin(
    pin_type: str, where_order: float, op: str, align: str
) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        stmt = select(PinResource.id, PinResource.order).where(PinResource.type == pin_type)
        stmt = stmt.where(
            PinResource.order < where_order if op == "lt" else PinResource.order > where_order
        )
        stmt = stmt.order_by(
            PinResource.order.desc() if align == "desc" else PinResource.order.asc()
        )
        row = (await session.execute(stmt)).first()
    return {"id": row[0], "order": row[1]} if row else None


async def update_pin_order(pin_id: str, new_order: float) -> None:
    async with db_engine.session() as session:
        await session.execute(
            PinResource.__table__.update()
            .where(PinResource.id == pin_id)
            .values(order=new_order)
        )
        await session.commit()


async def shuffle_pins(user_id: str, anchor_order: float, op: str, delta: int) -> None:
    async with db_engine.session() as session:
        await session.execute(
            PinResource.__table__.update()
            .where(
                PinResource.created_by == user_id,
                PinResource.order < anchor_order
                if op == "lt"
                else PinResource.order > anchor_order,
            )
            .values(order=PinResource.order + delta)
        )
        await session.commit()


# --- resource fetchers -----------------------------------------------------


async def fetch_bases(ids: list[str]) -> list[dict[str, Any]]:
    if not ids:
        return []
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(Base.id, Base.name, Base.icon).where(
                    Base.id.in_(ids), Base.deleted_time.is_(None)
                )
            )
        ).all()
    return [{"id": r[0], "name": r[1], "icon": r[2]} for r in rows]


async def fetch_spaces(ids: list[str]) -> list[dict[str, Any]]:
    if not ids:
        return []
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(Space.id, Space.name).where(
                    Space.id.in_(ids), Space.deleted_time.is_(None)
                )
            )
        ).all()
    return [{"id": r[0], "name": r[1]} for r in rows]


async def fetch_tables(ids: list[str]) -> list[dict[str, Any]]:
    if not ids:
        return []
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(TableMeta.id, TableMeta.name, TableMeta.base_id, TableMeta.icon).where(
                    TableMeta.id.in_(ids), TableMeta.deleted_time.is_(None)
                )
            )
        ).all()
    return [{"id": r[0], "name": r[1], "baseId": r[2], "icon": r[3]} for r in rows]


async def fetch_views(ids: list[str]) -> list[dict[str, Any]]:
    if not ids:
        return []
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(
                    View.id,
                    View.name,
                    TableMeta.base_id,
                    TableMeta.id,
                    View.type,
                    View.options,
                )
                .join(TableMeta, View.table_id == TableMeta.id, isouter=True)
                .where(
                    View.id.in_(ids),
                    View.deleted_time.is_(None),
                    TableMeta.deleted_time.is_(None),
                )
            )
        ).all()
    return [
        {
            "id": r[0],
            "name": r[1],
            "baseId": r[2],
            "tableId": r[3],
            "type": r[4],
            "options": r[5],
        }
        for r in rows
    ]


async def fetch_dashboards(ids: list[str]) -> list[dict[str, Any]]:
    if not ids:
        return []
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(Dashboard.id, Dashboard.name, Dashboard.base_id).where(
                    Dashboard.id.in_(ids)
                )
            )
        ).all()
    return [{"id": r[0], "name": r[1], "baseId": r[2]} for r in rows]


# --- entry-map queries -----------------------------------------------------


async def find_pins_for_entry(user_id: str) -> list[dict[str, Any]]:
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(PinResource.resource_id, PinResource.type).where(
                    PinResource.created_by == user_id,
                    PinResource.type.in_(["base", "table"]),
                )
            )
        ).all()
    return [{"resourceId": r[0], "type": r[1]} for r in rows]


async def tables_by_ids(ids: list[str]) -> list[dict[str, Any]]:
    if not ids:
        return []
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(TableMeta.id, TableMeta.base_id).where(
                    TableMeta.id.in_(ids), TableMeta.deleted_time.is_(None)
                )
            )
        ).all()
    return [{"id": r[0], "baseId": r[1]} for r in rows]


async def node_visits(user_id: str, base_ids: list[str]) -> list[dict[str, Any]]:
    if not base_ids:
        return []
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(
                    UserLastVisit.parent_resource_id,
                    UserLastVisit.resource_id,
                    UserLastVisit.resource_type,
                )
                .where(
                    UserLastVisit.user_id == user_id,
                    UserLastVisit.parent_resource_id.in_(base_ids),
                    UserLastVisit.resource_type.in_(["table", "dashboard", "workflow", "app"]),
                )
                .order_by(UserLastVisit.last_visit_time.desc())
            )
        ).all()
    return [{"parentResourceId": r[0], "resourceId": r[1], "resourceType": r[2]} for r in rows]


async def first_nodes_by_base(base_ids: list[str]) -> list[dict[str, Any]]:
    if not base_ids:
        return []
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(BaseNode.base_id, BaseNode.resource_type, BaseNode.resource_id)
                .where(BaseNode.base_id.in_(base_ids))
                .order_by(BaseNode.base_id.asc(), BaseNode.order.asc())
            )
        ).all()
    return [{"baseId": r[0], "resourceType": r[1], "resourceId": r[2]} for r in rows]


async def view_visits(user_id: str, table_ids: list[str]) -> list[dict[str, Any]]:
    if not table_ids:
        return []
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(UserLastVisit.parent_resource_id, UserLastVisit.resource_id)
                .where(
                    UserLastVisit.user_id == user_id,
                    UserLastVisit.resource_type == "view",
                    UserLastVisit.parent_resource_id.in_(table_ids),
                )
                .order_by(UserLastVisit.last_visit_time.desc())
            )
        ).all()
    return [{"parentResourceId": r[0], "resourceId": r[1]} for r in rows]


async def views_by_tables(table_ids: list[str]) -> list[dict[str, Any]]:
    if not table_ids:
        return []
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(View.id, View.table_id)
                .where(View.table_id.in_(table_ids), View.deleted_time.is_(None))
                .order_by(View.order.asc())
            )
        ).all()
    return [{"id": r[0], "tableId": r[1]} for r in rows]
