"""space / collaborator / invitation / base persistence.

Raw-row access only — business rules live in service.py. Rows are plain dicts
with snake_case column names.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select, update

from ...db import engine as db_engine
from ...db.models_meta import Base, Collaborator, Invitation, InvitationRecord, Space, User


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ---- space ------------------------------------------------------------------


async def insert_space(space_id: str, name: str, created_by: str) -> dict[str, Any]:
    async with db_engine.session() as session:
        row = (
            (
                await session.execute(
                    Space.__table__.insert()
                    .values(id=space_id, name=name, created_by=created_by)
                    .returning(Space.__table__)
                )
            )
            .mappings()
            .first()
        )
        await session.commit()
        return dict(row)


async def get_space_row(space_id: str, include_deleted: bool = False) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        stmt = select(Space).where(Space.id == space_id)
        if not include_deleted:
            stmt = stmt.where(Space.deleted_time.is_(None))
        row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    return {c.name: getattr(row, c.name) for c in Space.__table__.columns}


async def list_space_rows_by_creator(created_by: str) -> list[dict[str, Any]]:
    async with db_engine.session() as session:
        rows = (
            (
                await session.execute(
                    select(Space)
                    .where(Space.created_by == created_by, Space.deleted_time.is_(None))
                    .order_by(Space.created_time.asc())
                )
            )
            .scalars()
            .all()
        )
    return [{c.name: getattr(r, c.name) for c in Space.__table__.columns} for r in rows]


async def list_space_rows_by_ids(space_ids: list[str]) -> list[dict[str, Any]]:
    async with db_engine.session() as session:
        rows = (
            (
                await session.execute(
                    select(Space)
                    .where(
                        Space.id.in_(space_ids),
                        Space.deleted_time.is_(None),
                        Space.is_template.is_(None),
                    )
                    .order_by(Space.created_time.asc())
                )
            )
            .scalars()
            .all()
        )
    return [{c.name: getattr(r, c.name) for c in Space.__table__.columns} for r in rows]


async def update_space_row(space_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        row = (
            (
                await session.execute(
                    update(Space)
                    .where(Space.id == space_id, Space.deleted_time.is_(None))
                    .values(**fields)
                    .returning(Space.__table__)
                )
            )
            .mappings()
            .first()
        )
        await session.commit()
        return dict(row) if row else None


async def soft_delete_space(space_id: str, user_id: str) -> bool:
    now = datetime.now(UTC).replace(tzinfo=None)
    async with db_engine.session() as session:
        result = await session.execute(
            update(Space)
            .where(Space.id == space_id, Space.deleted_time.is_(None))
            .values(deleted_time=now, last_modified_by=user_id)
        )
        await session.commit()
        return result.rowcount > 0


async def delete_space_row(space_id: str) -> None:
    async with db_engine.session() as session:
        await session.execute(delete(Space).where(Space.id == space_id))
        await session.commit()


# ---- base -------------------------------------------------------------------


async def list_base_rows_by_space(space_id: str) -> list[dict[str, Any]]:
    async with db_engine.session() as session:
        rows = (
            (
                await session.execute(
                    select(Base)
                    .where(Base.space_id == space_id, Base.deleted_time.is_(None))
                    .order_by(Base.order.asc())
                )
            )
            .scalars()
            .all()
        )
    return [{c.name: getattr(r, c.name) for c in Base.__table__.columns} for r in rows]


async def list_base_ids_by_space(space_id: str) -> list[str]:
    async with db_engine.session() as session:
        return list(
            (
                await session.execute(
                    select(Base.id).where(Base.space_id == space_id, Base.deleted_time.is_(None))
                )
            ).scalars()
        )


async def delete_base_row(base_id: str) -> None:
    async with db_engine.session() as session:
        await session.execute(delete(Base).where(Base.id == base_id))
        await session.commit()


# ---- collaborator -----------------------------------------------------------


async def insert_collaborator(fields: dict[str, Any]) -> None:
    async with db_engine.session() as session:
        await session.execute(Collaborator.__table__.insert().values(**fields))
        await session.commit()


async def insert_collaborators(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    async with db_engine.session() as session:
        await session.execute(Collaborator.__table__.insert().values(rows))
        await session.commit()


async def list_collaborator_rows_by_principals(
    principal_ids: list[str],
) -> list[dict[str, Any]]:
    async with db_engine.session() as session:
        rows = (
            (
                await session.execute(
                    select(Collaborator).where(Collaborator.principal_id.in_(principal_ids))
                )
            )
            .scalars()
            .all()
        )
    return [{c.name: getattr(r, c.name) for c in Collaborator.__table__.columns} for r in rows]


async def list_collaborators_by_principals_and_resources(
    principal_ids: list[str], resource_ids: list[str]
) -> list[dict[str, Any]]:
    async with db_engine.session() as session:
        rows = (
            (
                await session.execute(
                    select(Collaborator).where(
                        Collaborator.principal_id.in_(principal_ids),
                        Collaborator.resource_id.in_(resource_ids),
                    )
                )
            )
            .scalars()
            .all()
        )
    return [{c.name: getattr(r, c.name) for c in Collaborator.__table__.columns} for r in rows]


async def list_collaborator_rows(
    resource_ids: list[str],
    principal_ids: list[str] | None = None,
    resource_type: str | None = None,
) -> list[dict[str, Any]]:
    async with db_engine.session() as session:
        stmt = select(Collaborator).where(Collaborator.resource_id.in_(resource_ids))
        if principal_ids is not None:
            stmt = stmt.where(Collaborator.principal_id.in_(principal_ids))
        if resource_type is not None:
            stmt = stmt.where(Collaborator.resource_type == resource_type)
        rows = (await session.execute(stmt)).scalars().all()
    return [{c.name: getattr(r, c.name) for c in Collaborator.__table__.columns} for r in rows]


async def count_collaborators(
    principal_ids: list[str], principal_types: list[str], resource_id: str, resource_type: str
) -> int:
    async with db_engine.session() as session:
        return int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(Collaborator)
                    .where(
                        Collaborator.principal_id.in_(principal_ids),
                        Collaborator.principal_type.in_(principal_types),
                        Collaborator.resource_id == resource_id,
                        Collaborator.resource_type == resource_type,
                    )
                )
            ).scalar_one()
        )


async def delete_collaborator_row(
    resource_id: str, resource_type: str, principal_id: str, principal_type: str
) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        row = (
            (
                await session.execute(
                    delete(Collaborator)
                    .where(
                        Collaborator.resource_id == resource_id,
                        Collaborator.resource_type == resource_type,
                        Collaborator.principal_id == principal_id,
                        Collaborator.principal_type == principal_type,
                    )
                    .returning(Collaborator.__table__)
                )
            )
            .mappings()
            .first()
        )
        await session.commit()
        return dict(row) if row else None


async def delete_collaborators_by_resource_ids(
    principal_ids: list[str], principal_types: list[str], resource_ids: list[str]
) -> int:
    async with db_engine.session() as session:
        result = await session.execute(
            delete(Collaborator).where(
                Collaborator.principal_id.in_(principal_ids),
                Collaborator.principal_type.in_(principal_types),
                Collaborator.resource_id.in_(resource_ids),
                Collaborator.resource_type == "base",
            )
        )
        await session.commit()
        return result.rowcount


async def update_collaborator_role(
    resource_id: str,
    resource_type: str,
    principal_id: str,
    principal_type: str,
    role: str,
    operator_id: str,
) -> int:
    async with db_engine.session() as session:
        result = await session.execute(
            update(Collaborator)
            .where(
                Collaborator.resource_id == resource_id,
                Collaborator.resource_type == resource_type,
                Collaborator.principal_id == principal_id,
                Collaborator.principal_type == principal_type,
            )
            .values(role_name=role, last_modified_by=operator_id)
        )
        await session.commit()
        return result.rowcount


async def list_collaborator_rows_for_space_tree(space_id: str) -> list[dict[str, Any]]:
    """Rows on the space plus every live base in it (the includeBase set)."""
    base_ids = await list_base_ids_by_space(space_id)
    resource_ids = [*base_ids, space_id]
    return await list_collaborator_rows(resource_ids)


async def count_space_tree_collaborators(space_id: str, include_base: bool) -> tuple[int, int]:
    """(total, uniqTotal) matching collaborator.service.getSpaceCollaboratorStats."""
    rows = (
        await list_collaborator_rows_for_space_tree(space_id)
        if include_base
        else (await list_collaborator_rows([space_id]))
    )
    # Non-system principals only: rows whose principal is a resolvable,
    # non-system user count towards uniqTotal; the row-level builder also
    # filters dangling principals through the users join.
    principal_ids = list({r["principal_id"] for r in rows})
    users = await list_user_rows_by_ids(principal_ids)
    user_map = {u["id"]: u for u in users if u["deleted_time"] is None}
    visible = [
        r
        for r in rows
        if r["principal_id"] in user_map and not user_map[r["principal_id"]].get("is_system")
    ]
    uniq = len({r["principal_id"] for r in visible})
    return len(visible), uniq


# ---- invitation -------------------------------------------------------------


async def insert_invitation(fields: dict[str, Any]) -> dict[str, Any]:
    async with db_engine.session() as session:
        row = (
            (
                await session.execute(
                    Invitation.__table__.insert().values(**fields).returning(Invitation.__table__)
                )
            )
            .mappings()
            .first()
        )
        await session.commit()
        return dict(row)


def _resource_column(resource_type: str) -> Any:
    return Invitation.base_id if resource_type == "base" else Invitation.space_id


async def list_invitation_link_rows(
    resource_id: str, resource_type: str = "space"
) -> list[dict[str, Any]]:
    column = _resource_column(resource_type)
    async with db_engine.session() as session:
        rows = (
            (
                await session.execute(
                    select(Invitation)
                    .where(
                        column == resource_id,
                        Invitation.type == "link",
                        Invitation.deleted_time.is_(None),
                    )
                    .order_by(Invitation.created_time.asc())
                )
            )
            .scalars()
            .all()
        )
    return [{c.name: getattr(r, c.name) for c in Invitation.__table__.columns} for r in rows]


async def get_invitation_link_row(
    invitation_id: str, resource_id: str, resource_type: str = "space"
) -> dict[str, Any] | None:
    column = _resource_column(resource_type)
    async with db_engine.session() as session:
        row = (
            (
                await session.execute(
                    select(Invitation).where(
                        Invitation.id == invitation_id,
                        column == resource_id,
                        Invitation.type == "link",
                    )
                )
            )
            .scalars()
            .first()
        )
    if row is None:
        return None
    return {c.name: getattr(row, c.name) for c in Invitation.__table__.columns}


async def update_invitation_row(
    invitation_id: str, fields: dict[str, Any]
) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        row = (
            (
                await session.execute(
                    update(Invitation)
                    .where(Invitation.id == invitation_id)
                    .values(**fields)
                    .returning(Invitation.__table__)
                )
            )
            .mappings()
            .first()
        )
        await session.commit()
        return dict(row) if row else None


async def delete_invitation_rows(resource_id: str, resource_type: str = "space") -> None:
    column = _resource_column(resource_type)
    async with db_engine.session() as session:
        await session.execute(delete(Invitation).where(column == resource_id))
        await session.commit()


async def get_invitation_row(invitation_id: str) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        row = (
            (
                await session.execute(
                    select(Invitation).where(
                        Invitation.id == invitation_id, Invitation.deleted_time.is_(None)
                    )
                )
            )
            .scalars()
            .first()
        )
    if row is None:
        return None
    return {c.name: getattr(row, c.name) for c in Invitation.__table__.columns}


async def insert_invitation_record(fields: dict[str, Any]) -> dict[str, Any]:
    async with db_engine.session() as session:
        row = (
            (
                await session.execute(
                    InvitationRecord.__table__.insert()
                    .values(**fields)
                    .returning(InvitationRecord.__table__)
                )
            )
            .mappings()
            .first()
        )
        await session.commit()
        return dict(row)


# ---- users ------------------------------------------------------------------


async def list_user_rows_by_ids(user_ids: list[str]) -> list[dict[str, Any]]:
    if not user_ids:
        return []
    async with db_engine.session() as session:
        rows = (await session.execute(select(User).where(User.id.in_(user_ids)))).scalars().all()
    return [{c.name: getattr(r, c.name) for c in User.__table__.columns} for r in rows]
