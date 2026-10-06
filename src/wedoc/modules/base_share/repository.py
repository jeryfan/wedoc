"""base-share persistence — raw-row access."""

from typing import Any

from sqlalchemy import delete, insert, select, update

from ...core.ids import cuid
from ...db import engine as db_engine
from ...db.models_meta import BaseShare, TableMeta, View


def _row(share: BaseShare) -> dict[str, Any]:
    return {
        "id": share.id,
        "baseId": share.base_id,
        "shareId": share.share_id,
        "password": share.password,
        "nodeId": share.node_id,
        "allowSave": share.allow_save,
        "allowCopy": share.allow_copy,
        "allowEdit": share.allow_edit,
        "enabled": share.enabled,
    }


async def find_by_base_node(base_id: str, node_id: str | None) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(BaseShare).where(
                    BaseShare.base_id == base_id, BaseShare.node_id.is_(node_id)
                    if node_id is None
                    else (BaseShare.node_id == node_id),
                )
            )
        ).scalar_one_or_none()
    return _row(row) if row else None


async def find_enabled_by_base_node(
    base_id: str, node_id: str | None
) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        cond = (
            BaseShare.node_id.is_(None) if node_id is None else (BaseShare.node_id == node_id)
        )
        row = (
            await session.execute(
                select(BaseShare).where(
                    BaseShare.base_id == base_id, cond, BaseShare.enabled.is_(True)
                )
            )
        ).scalar_one_or_none()
    return _row(row) if row else None


async def find_enabled_by_base_share(
    base_id: str, share_id: str
) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(BaseShare).where(
                    BaseShare.base_id == base_id,
                    BaseShare.share_id == share_id,
                    BaseShare.enabled.is_(True),
                )
            )
        ).scalar_one_or_none()
    return _row(row) if row else None


async def find_by_share_id(share_id: str) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(BaseShare).where(BaseShare.share_id == share_id)
            )
        ).scalar_one_or_none()
    return _row(row) if row else None


async def list_enabled(base_id: str) -> list[dict[str, Any]]:
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(BaseShare.node_id)
                .where(BaseShare.base_id == base_id, BaseShare.enabled.is_(True))
                .order_by(BaseShare.created_time.desc())
            )
        ).all()
    return [{"nodeId": r[0]} for r in rows]


async def delete_by_id(id_: str) -> None:
    async with db_engine.session() as session:
        await session.execute(delete(BaseShare).where(BaseShare.id == id_))
        await session.commit()


async def delete_by_base_node(base_id: str, node_id: str) -> list[str]:
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                delete(BaseShare)
                .where(BaseShare.base_id == base_id, BaseShare.node_id == node_id)
                .returning(BaseShare.share_id)
            )
        ).all()
        await session.commit()
    return [r[0] for r in rows]


async def create(
    base_id: str, share_id: str, node_id: str | None, created_by: str
) -> dict[str, Any]:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                insert(BaseShare)
                .values(
                    id=cuid(),
                    base_id=base_id,
                    share_id=share_id,
                    node_id=node_id,
                    created_by=created_by,
                )
                .returning(BaseShare)
            )
        ).scalar_one()
        await session.commit()
        return _row(row)


async def update_by_id(id_: str, values: dict[str, Any]) -> dict[str, Any]:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                update(BaseShare).where(BaseShare.id == id_).values(**values).returning(BaseShare)
            )
        ).scalar_one()
        await session.commit()
        return _row(row)


async def is_editable_node(node_id: str) -> bool:
    # base_node resource types Table / Folder are editable; wedoc syncs only
    # folder nodes today, so a table node may resolve via table_meta instead.
    from ...db.models_meta import BaseNode

    async with db_engine.session() as session:
        resource_type = (
            await session.execute(
                select(BaseNode.resource_type).where(BaseNode.id == node_id)
            )
        ).scalar_one_or_none()
    return resource_type in ("table", "folder")


async def first_table_id(base_id: str) -> str | None:
    async with db_engine.session() as session:
        return (
            await session.execute(
                select(TableMeta.id)
                .where(TableMeta.base_id == base_id, TableMeta.deleted_time.is_(None))
                .order_by(TableMeta.order.asc())
            )
        ).scalars().first()


async def first_view_id(table_id: str) -> str | None:
    async with db_engine.session() as session:
        return (
            await session.execute(
                select(View.id)
                .where(View.table_id == table_id, View.deleted_time.is_(None))
                .order_by(View.order.asc())
            )
        ).scalars().first()


async def list_base_nodes(base_id: str) -> list[dict[str, Any]]:
    from ...db.models_meta import BaseNode

    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(
                    BaseNode.id,
                    BaseNode.parent_id,
                    BaseNode.resource_type,
                    BaseNode.resource_id,
                    BaseNode.order,
                )
                .where(BaseNode.base_id == base_id)
                .order_by(BaseNode.order.asc())
            )
        ).all()
    return [
        {
            "id": r[0],
            "parentId": r[1],
            "resourceType": r[2],
            "resourceId": r[3],
            "order": r[4],
        }
        for r in rows
    ]
