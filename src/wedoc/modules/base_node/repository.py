"""base-node / base-node-folder persistence.

Raw-row access only — business rules live in service.py. Rows are plain dicts
with snake_case column names.
"""

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ...db import engine as db_engine
from ...db.models_meta import BaseNode, BaseNodeFolder, Dashboard, TableMeta

_UNSET: Any = object()


def _node_row(instance: BaseNode) -> dict[str, Any]:
    return {c.name: getattr(instance, c.name) for c in BaseNode.__table__.columns}


async def get_node_row(node_id: str) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        row = (
            (await session.execute(select(BaseNode).where(BaseNode.id == node_id)))
            .scalars()
            .first()
        )
    return _node_row(row) if row else None


async def get_node_id_by_resource_id(base_id: str, resource_id: str) -> str | None:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(BaseNode.id).where(
                    BaseNode.base_id == base_id,
                    BaseNode.resource_id == resource_id,
                )
            )
        ).first()
    return row[0] if row else None


async def get_node_by_resource(
    base_id: str, resource_type: str, resource_id: str
) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        row = (
            (
                await session.execute(
                    select(BaseNode).where(
                        BaseNode.base_id == base_id,
                        BaseNode.resource_type == resource_type,
                        BaseNode.resource_id == resource_id,
                    )
                )
            )
            .scalars()
            .first()
        )
    return _node_row(row) if row else None


async def list_node_rows(base_id: str) -> list[dict[str, Any]]:
    async with db_engine.session() as session:
        rows = (
            (
                await session.execute(
                    select(BaseNode)
                    .where(BaseNode.base_id == base_id)
                    .order_by(BaseNode.order.asc())
                )
            )
            .scalars()
            .all()
        )
    return [_node_row(r) for r in rows]


async def list_folder_node_rows(base_id: str) -> list[dict[str, Any]]:
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(BaseNode.id, BaseNode.parent_id).where(
                    BaseNode.base_id == base_id,
                    BaseNode.resource_type == "folder",
                )
            )
        ).all()
    return [{"id": r[0], "parent_id": r[1]} for r in rows]


async def insert_node(fields: dict[str, Any]) -> dict[str, Any]:
    async with db_engine.session() as session:
        row = (
            (
                await session.execute(
                    BaseNode.__table__.insert().values(**fields).returning(BaseNode.__table__)
                )
            )
            .mappings()
            .first()
        )
        await session.commit()
        return dict(row)


async def upsert_node(
    base_id: str,
    resource_type: str,
    resource_id: str,
    create_fields: dict[str, Any],
    update_fields: dict[str, Any],
) -> dict[str, Any]:
    """Insert or, on the (baseId, resourceType, resourceId) unique key, update.

    Mirrors the reference upsert so a node-list reconciliation that already
    materialised a root-level row for a resource created out of band is adopted
    instead of colliding.
    """
    async with db_engine.session() as session:
        stmt = (
            pg_insert(BaseNode)
            .values(
                base_id=base_id,
                resource_type=resource_type,
                resource_id=resource_id,
                **create_fields,
            )
            .on_conflict_do_update(
                index_elements=["base_id", "resource_type", "resource_id"],
                set_=update_fields,
            )
            .returning(BaseNode.__table__)
        )
        row = (await session.execute(stmt)).mappings().first()
        await session.commit()
        return dict(row)


async def update_node_row(node_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
    from sqlalchemy import update

    async with db_engine.session() as session:
        row = (
            (
                await session.execute(
                    update(BaseNode)
                    .where(BaseNode.id == node_id)
                    .values(**fields)
                    .returning(BaseNode.__table__)
                )
            )
            .mappings()
            .first()
        )
        await session.commit()
        return dict(row) if row else None


async def delete_node_row(node_id: str) -> dict[str, Any] | None:
    from sqlalchemy import delete

    async with db_engine.session() as session:
        row = (
            (
                await session.execute(
                    delete(BaseNode).where(BaseNode.id == node_id).returning(BaseNode.__table__)
                )
            )
            .mappings()
            .first()
        )
        await session.commit()
        return dict(row) if row else None


async def delete_node_rows(node_ids: list[str]) -> None:
    from sqlalchemy import delete

    if not node_ids:
        return
    async with db_engine.session() as session:
        await session.execute(delete(BaseNode).where(BaseNode.id.in_(node_ids)))
        await session.commit()


async def get_max_order(base_id: str, parent_id: Any = _UNSET) -> float:
    async with db_engine.session() as session:
        stmt = select(func.max(BaseNode.order)).where(BaseNode.base_id == base_id)
        # prisma `where: {baseId, parentId}`: an omitted parentId is not a
        # filter; an explicit null matches root-level rows only.
        if parent_id is not _UNSET:
            stmt = stmt.where(
                BaseNode.parent_id.is_(None)
                if parent_id is None
                else BaseNode.parent_id == parent_id
            )
        value = (await session.execute(stmt)).scalar()
    return float(value) if value is not None else 0.0


async def list_child_orders(node_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    """children select: {id, order} ordered by order asc, grouped by parent."""
    if not node_ids:
        return {}
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(BaseNode.parent_id, BaseNode.id, BaseNode.order)
                .where(BaseNode.parent_id.in_(node_ids))
                .order_by(BaseNode.order.asc())
            )
        ).all()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for parent_id, child_id, order in rows:
        grouped.setdefault(parent_id, []).append({"id": child_id, "order": order})
    return grouped


# ---- folder resources --------------------------------------------------------


async def insert_folder(fields: dict[str, Any]) -> dict[str, Any]:
    async with db_engine.session() as session:
        row = (
            (
                await session.execute(
                    BaseNodeFolder.__table__.insert()
                    .values(**fields)
                    .returning(BaseNodeFolder.__table__)
                )
            )
            .mappings()
            .first()
        )
        await session.commit()
        return dict(row)


async def get_folder_row(folder_id: str) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        row = (
            (await session.execute(select(BaseNodeFolder).where(BaseNodeFolder.id == folder_id)))
            .scalars()
            .first()
        )
    if row is None:
        return None
    return {c.name: getattr(row, c.name) for c in BaseNodeFolder.__table__.columns}


async def list_folder_rows(base_id: str, ids: list[str] | None = None) -> list[dict[str, Any]]:
    async with db_engine.session() as session:
        stmt = select(BaseNodeFolder).where(BaseNodeFolder.base_id == base_id)
        if ids is not None:
            if not ids:
                return []
            stmt = stmt.where(BaseNodeFolder.id.in_(ids))
        rows = (await session.execute(stmt)).scalars().all()
    return [{c.name: getattr(r, c.name) for c in BaseNodeFolder.__table__.columns} for r in rows]


async def list_folder_names(base_id: str) -> list[str]:
    async with db_engine.session() as session:
        return list(
            (
                await session.execute(
                    select(BaseNodeFolder.name).where(BaseNodeFolder.base_id == base_id)
                )
            ).scalars()
        )


async def find_folder_by_name(
    base_id: str, name: str, exclude_id: str | None
) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        stmt = select(BaseNodeFolder).where(
            BaseNodeFolder.base_id == base_id, BaseNodeFolder.name == name
        )
        if exclude_id is not None:
            stmt = stmt.where(BaseNodeFolder.id != exclude_id)
        row = (await session.execute(stmt)).scalars().first()
    if row is None:
        return None
    return {c.name: getattr(row, c.name) for c in BaseNodeFolder.__table__.columns}


async def update_folder_row(folder_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
    from sqlalchemy import update

    async with db_engine.session() as session:
        row = (
            (
                await session.execute(
                    update(BaseNodeFolder)
                    .where(BaseNodeFolder.id == folder_id)
                    .values(**fields)
                    .returning(BaseNodeFolder.__table__)
                )
            )
            .mappings()
            .first()
        )
        await session.commit()
        return dict(row) if row else None


async def delete_folder_row(base_id: str, folder_id: str) -> dict[str, Any] | None:
    from sqlalchemy import delete

    async with db_engine.session() as session:
        row = (
            (
                await session.execute(
                    delete(BaseNodeFolder)
                    .where(BaseNodeFolder.base_id == base_id, BaseNodeFolder.id == folder_id)
                    .returning(BaseNodeFolder.__table__)
                )
            )
            .mappings()
            .first()
        )
        await session.commit()
        return dict(row) if row else None


# ---- table resources ---------------------------------------------------------


async def list_table_resource_rows(
    base_id: str, ids: list[str] | None = None
) -> list[dict[str, Any]]:
    """Ports getTableResources: ready, non-deleted tables with the audit columns
    the node resource meta exposes (no defaultViewId — the list VO omits it)."""
    async with db_engine.session() as session:
        stmt = select(
            TableMeta.id,
            TableMeta.name,
            TableMeta.icon,
            TableMeta.created_by,
            TableMeta.created_time,
            TableMeta.last_modified_by,
            TableMeta.last_modified_time,
        ).where(
            TableMeta.base_id == base_id,
            TableMeta.deleted_time.is_(None),
            TableMeta.provision_state == "ready",
        )
        if ids is not None:
            if not ids:
                return []
            stmt = stmt.where(TableMeta.id.in_(ids))
        rows = (await session.execute(stmt)).mappings().all()
    return [dict(r) for r in rows]


# ---- dashboard resources -----------------------------------------------------


async def list_dashboard_resource_rows(
    base_id: str, ids: list[str] | None = None
) -> list[dict[str, Any]]:
    """Ports getDashboardResources: dashboards with the audit columns the node
    resource meta exposes."""
    async with db_engine.session() as session:
        stmt = select(
            Dashboard.id,
            Dashboard.name,
            Dashboard.created_by,
            Dashboard.created_time,
            Dashboard.last_modified_by,
            Dashboard.last_modified_time,
        ).where(Dashboard.base_id == base_id)
        if ids is not None:
            if not ids:
                return []
            stmt = stmt.where(Dashboard.id.in_(ids))
        rows = (await session.execute(stmt)).mappings().all()
    return [dict(r) for r in rows]
