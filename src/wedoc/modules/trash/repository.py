"""Trash persistence — raw-row access for the space/base/table trash paths."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ...db import engine as db_engine
from ...db.models_meta import (
    Base,
    Collaborator,
    Field,
    RecordTrash,
    Space,
    TableMeta,
    TableTrash,
    Trash,
    User,
    View,
)

_OWNER_CREATOR = ("owner", "creator")


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _to_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).replace(tzinfo=None)


async def upsert_trash(
    trash_id: str,
    resource_type: str,
    resource_id: str,
    parent_id: str | None,
    deleted_time: datetime,
    deleted_by: str,
) -> None:
    async with db_engine.session() as session:
        stmt = pg_insert(Trash).values(
            id=trash_id,
            resource_type=resource_type,
            resource_id=resource_id,
            parent_id=parent_id,
            deleted_time=deleted_time,
            deleted_by=deleted_by,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[Trash.resource_type, Trash.resource_id],
            set_={"deleted_time": deleted_time, "deleted_by": deleted_by},
        )
        await session.execute(stmt)
        await session.commit()


async def find_trash(trash_id: str) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        row = (
            await session.execute(select(Trash).where(Trash.id == trash_id))
        ).scalar_one_or_none()
    if row is None:
        return None
    return {
        "id": row.id,
        "resourceType": row.resource_type,
        "resourceId": row.resource_id,
        "parentId": row.parent_id,
        "deletedTime": row.deleted_time,
        "deletedBy": row.deleted_by,
    }


async def delete_trash(trash_id: str) -> None:
    async with db_engine.session() as session:
        await session.execute(delete(Trash).where(Trash.id == trash_id))
        await session.commit()


async def authorized_resources(user_id: str) -> tuple[list[str], list[str]]:
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(Collaborator.resource_id, Collaborator.resource_type).where(
                    Collaborator.principal_id == user_id,
                    Collaborator.role_name.in_(_OWNER_CREATOR),
                )
            )
        ).all()
    space_ids: list[str] = []
    base_ids: list[str] = []
    for resource_id, resource_type in rows:
        if resource_type == "base":
            base_ids.append(resource_id)
        elif resource_type == "space":
            space_ids.append(resource_id)
    return list(dict.fromkeys(space_ids)), list(dict.fromkeys(base_ids))


async def list_spaces(space_ids: list[str]) -> list[dict[str, Any]]:
    if not space_ids:
        return []
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(Space.id, Space.name, Space.avatar).where(Space.id.in_(space_ids))
            )
        ).all()
    return [{"id": r[0], "name": r[1], "avatar": r[2]} for r in rows]


async def list_bases(space_ids: list[str], base_ids: list[str]) -> list[dict[str, Any]]:
    if not space_ids and not base_ids:
        return []
    async with db_engine.session() as session:
        stmt = (
            select(Base.id, Base.name, Base.space_id, Space.name.label("space_name"))
            .join(Space, Space.id == Base.space_id)
        )
        stmt = stmt.where(Base.space_id.in_(space_ids) | Base.id.in_(base_ids))
        rows = (await session.execute(stmt)).all()
    return [
        {"id": r[0], "name": r[1], "spaceId": r[2], "spaceName": r[3]} for r in rows
    ]


async def list_trash_by_resource_ids(resource_ids: list[str]) -> list[dict[str, Any]]:
    if not resource_ids:
        return []
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(Trash)
                .where(Trash.resource_id.in_(resource_ids))
                .order_by(Trash.deleted_time.desc())
            )
        ).scalars().all()
    return [_trash_dict(r) for r in rows]


async def list_trashed_space_ids(space_ids: list[str]) -> list[str]:
    if not space_ids:
        return []
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(Trash.resource_id).where(
                    Trash.resource_type == "space", Trash.resource_id.in_(space_ids)
                )
            )
        ).all()
    return [r[0] for r in rows]


async def list_base_trash(
    authorized_base_ids: list[str], parent_in: list[str] | None, not_in_spaces: list[str]
) -> list[dict[str, Any]]:
    if not authorized_base_ids:
        return []
    async with db_engine.session() as session:
        stmt = select(Trash).where(
            Trash.resource_type == "base",
            Trash.resource_id.in_(authorized_base_ids),
        )
        if not_in_spaces:
            stmt = stmt.where(Trash.parent_id.notin_(not_in_spaces))
        if parent_in is not None:
            stmt = stmt.where(Trash.parent_id.in_(parent_in))
        rows = (await session.execute(stmt)).scalars().all()
    return [_trash_dict(r) for r in rows]


async def list_trash_by_parent(
    parent_id: str, cursor: str | None, limit: int
) -> list[dict[str, Any]]:
    async with db_engine.session() as session:
        cursor_time = None
        if cursor:
            cursor_time = (
                await session.execute(
                    select(Trash.deleted_time).where(Trash.id == cursor)
                )
            ).scalar_one_or_none()
        stmt = select(Trash).where(Trash.parent_id == parent_id)
        if cursor_time is not None:
            stmt = stmt.where(
                (Trash.deleted_time < cursor_time)
                | ((Trash.deleted_time == cursor_time) & (Trash.id <= cursor))
            )
        stmt = stmt.order_by(Trash.deleted_time.desc(), Trash.id.desc()).limit(limit)
        rows = (await session.execute(stmt)).scalars().all()
    return [_trash_dict(r) for r in rows]


async def find_trashed_space(space_id: str) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(Trash).where(
                    Trash.resource_id == space_id, Trash.resource_type == "space"
                )
            )
        ).scalar_one_or_none()
    return _trash_dict(row) if row else None


async def parent_chain_trashed(parent_id: str) -> bool:
    query = text(
        """
        WITH RECURSIVE parent_chain AS (
            SELECT resource_id, parent_id FROM trash WHERE resource_id = :pid
            UNION ALL
            SELECT t.resource_id, t.parent_id FROM trash t
            JOIN parent_chain pc ON t.resource_id = pc.parent_id
            WHERE pc.parent_id IS NOT NULL
        )
        SELECT resource_id FROM parent_chain LIMIT 1
        """
    )
    async with db_engine.session() as session:
        row = (await session.execute(query, {"pid": parent_id})).first()
    return row is not None


async def base_of(base_id: str) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(Base.id, Base.space_id).where(Base.id == base_id)
            )
        ).first()
    return {"id": row[0], "spaceId": row[1]} if row else None


async def restore_space(space_id: str) -> None:
    async with db_engine.session() as session:
        await session.execute(
            update(Space).where(Space.id == space_id).values(deleted_time=None)
        )
        await session.commit()


async def restore_base(base_id: str) -> None:
    async with db_engine.session() as session:
        await session.execute(
            update(Base).where(Base.id == base_id).values(deleted_time=None)
        )
        await session.commit()


async def restore_table(table_id: str) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        base_id = (
            await session.execute(
                select(TableMeta.base_id).where(TableMeta.id == table_id)
            )
        ).scalar_one_or_none()
        if base_id is None:
            return None
        await session.execute(
            update(TableMeta)
            .where(TableMeta.id == table_id)
            .values(deleted_time=None, provision_state="ready")
        )
        await session.commit()
    return {"baseId": base_id}


async def base_of_table(table_id: str) -> str | None:
    async with db_engine.session() as session:
        return (
            await session.execute(
                select(TableMeta.base_id).where(TableMeta.id == table_id)
            )
        ).scalar_one_or_none()


async def list_trashed_tables(base_id: str) -> list[dict[str, Any]]:
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(TableMeta.id, TableMeta.name).where(
                    TableMeta.base_id == base_id, TableMeta.deleted_time.is_not(None)
                )
            )
        ).all()
    return [{"id": r[0], "name": r[1]} for r in rows]


async def user_info_list(user_ids: list[str]) -> list[dict[str, Any]]:
    if not user_ids:
        return []
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(User.id, User.name, User.email, User.avatar).where(
                    User.id.in_(user_ids)
                )
            )
        ).all()
    return [
        {"id": r[0], "name": r[1], "email": r[2], "avatar": r[3]} for r in rows
    ]


def _trash_dict(row: Trash) -> dict[str, Any]:
    return {
        "id": row.id,
        "resourceType": row.resource_type,
        "resourceId": row.resource_id,
        "parentId": row.parent_id,
        "deletedTime": row.deleted_time,
        "deletedBy": row.deleted_by,
    }


async def insert_table_trash(
    trash_id: str, table_id: str, resource_type: str, snapshot: str, created_by: str
) -> None:
    async with db_engine.session() as session:
        await session.execute(
            pg_insert(TableTrash).values(
                id=trash_id,
                table_id=table_id,
                resource_type=resource_type,
                snapshot=snapshot,
                created_by=created_by,
            )
        )
        await session.commit()


async def insert_record_trash(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    async with db_engine.session() as session:
        await session.execute(pg_insert(RecordTrash), rows)
        await session.commit()


async def list_table_trash(
    table_id: str,
    cursor: str | None,
    limit: int,
    resource_types: list[str] | None,
    deleted_by: list[str] | None,
    deleted_time_start: str | None,
    deleted_time_end: str | None,
) -> list[dict[str, Any]]:
    async with db_engine.session() as session:
        cursor_time = None
        if cursor:
            cursor_time = (
                await session.execute(
                    select(TableTrash.created_time).where(TableTrash.id == cursor)
                )
            ).scalar_one_or_none()
        stmt = select(TableTrash).where(TableTrash.table_id == table_id)
        if resource_types:
            stmt = stmt.where(TableTrash.resource_type.in_(resource_types))
        if deleted_by:
            stmt = stmt.where(TableTrash.created_by.in_(deleted_by))
        if deleted_time_start:
            stmt = stmt.where(TableTrash.created_time >= _to_datetime(deleted_time_start))
        if deleted_time_end:
            stmt = stmt.where(TableTrash.created_time <= _to_datetime(deleted_time_end))
        if cursor_time is not None:
            stmt = stmt.where(TableTrash.created_time < cursor_time)
        stmt = stmt.order_by(TableTrash.created_time.desc()).limit(limit)
        rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": r.id,
            "tableId": r.table_id,
            "resourceType": r.resource_type,
            "snapshot": r.snapshot,
            "createdTime": r.created_time,
            "createdBy": r.created_by,
        }
        for r in rows
    ]


async def find_table_trash(trash_id: str, table_id: str) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(TableTrash).where(
                    TableTrash.id == trash_id, TableTrash.table_id == table_id
                )
            )
        ).scalar_one_or_none()
    if row is None:
        return None
    return {
        "id": row.id,
        "tableId": row.table_id,
        "resourceType": row.resource_type,
        "snapshot": row.snapshot,
        "createdTime": row.created_time,
        "createdBy": row.created_by,
    }


async def delete_table_trash(trash_id: str, table_id: str) -> None:
    async with db_engine.session() as session:
        await session.execute(
            delete(TableTrash).where(
                TableTrash.id == trash_id, TableTrash.table_id == table_id
            )
        )
        await session.commit()


async def delete_record_trash(table_id: str, record_ids: list[str]) -> None:
    if not record_ids:
        return
    async with db_engine.session() as session:
        await session.execute(
            delete(RecordTrash).where(
                RecordTrash.table_id == table_id,
                RecordTrash.record_id.in_(record_ids),
            )
        )
        await session.commit()


async def list_record_trash(table_id: str, record_ids: list[str]) -> list[dict[str, Any]]:
    if not record_ids:
        return []
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(RecordTrash)
                .where(
                    RecordTrash.table_id == table_id,
                    RecordTrash.record_id.in_(record_ids),
                )
                .order_by(RecordTrash.created_time.desc(), RecordTrash.id.desc())
            )
        ).scalars().all()
    return [
        {
            "id": r.id,
            "recordId": r.record_id,
            "snapshot": r.snapshot,
            "createdTime": r.created_time,
            "createdBy": r.created_by,
        }
        for r in rows
    ]


async def list_deleted_views(view_ids: list[str]) -> list[dict[str, Any]]:
    if not view_ids:
        return []
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(View.id, View.name, View.type).where(
                    View.id.in_(view_ids), View.deleted_time.is_not(None)
                )
            )
        ).all()
    return [{"id": r[0], "name": r[1], "type": r[2]} for r in rows]


async def list_deleted_fields(field_ids: list[str]) -> list[dict[str, Any]]:
    if not field_ids:
        return []
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(
                    Field.id,
                    Field.name,
                    Field.type,
                    Field.options,
                    Field.is_lookup,
                    Field.is_conditional_lookup,
                ).where(Field.id.in_(field_ids), Field.deleted_time.is_not(None))
            )
        ).all()
    return [
        {
            "id": r[0],
            "name": r[1],
            "type": r[2],
            "options": r[3],
            "isLookup": r[4],
            "isConditionalLookup": r[5],
        }
        for r in rows
    ]
