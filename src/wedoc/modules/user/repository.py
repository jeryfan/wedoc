"""users / account / attachments / user_last_visit persistence.

Raw-row access only — no business rules here (that lives in service.py).
Rows are returned as plain dicts with snake_case column names.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select, update

from ...core.ids import cuid
from ...db import engine as db_engine
from ...db.models_meta import Account, Attachments, User, UserLastVisit


def _user_dict(row: User) -> dict[str, Any]:
    return {c.name: getattr(row, c.name) for c in User.__table__.columns}


async def get_user_row_by_id(user_id: str) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(User).where(User.id == user_id, User.deleted_time.is_(None))
            )
        ).scalar_one_or_none()
    return _user_dict(row) if row is not None else None


async def get_user_row_by_email(email: str) -> dict[str, Any] | None:
    """User by lowercased email, with the ``accounts`` relation like the prisma query."""
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(User).where(
                    User.email == email.lower(), User.deleted_time.is_(None)
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        user = _user_dict(row)
        accounts = (
            (
                await session.execute(
                    select(Account).where(Account.user_id == user["id"])
                )
            )
            .scalars()
            .all()
        )
        user["accounts"] = [
            {c.name: getattr(account, c.name) for c in Account.__table__.columns}
            for account in accounts
        ]
    return user


async def has_non_system_user() -> bool:
    async with db_engine.session() as session:
        row = (
            await session.execute(select(User.id).where(User.is_system.is_(None)).limit(1))
        ).first()
    return row is not None


async def create_user_row(data: dict[str, Any]) -> dict[str, Any]:
    async with db_engine.session() as session:
        row = User(**data)
        session.add(row)
        await session.commit()
    return _user_dict(row)


async def create_account_row(user_id: str, provider: str, provider_id: str, type_: str) -> None:
    async with db_engine.session() as session:
        session.add(
            Account(
                id=cuid(), user_id=user_id, provider=provider, provider_id=provider_id, type=type_
            )
        )
        await session.commit()


async def update_user_row(
    user_id: str, data: dict[str, Any], *, only_with_password_null: bool = False
) -> None:
    async with db_engine.session() as session:
        stmt = update(User).where(User.id == user_id, User.deleted_time.is_(None))
        if only_with_password_null:
            stmt = stmt.where(User.password.is_(None))
        await session.execute(stmt.values(**data))
        await session.commit()


async def update_notify_meta(user_id: str, merged: dict[str, Any]) -> None:
    import json

    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(User.notify_meta)
                .where(User.id == user_id, User.deleted_time.is_(None))
                .with_for_update()
            )
        ).first()
        prev: dict[str, Any] = {}
        if row and row[0]:
            try:
                prev = json.loads(row[0])
            except (json.JSONDecodeError, TypeError):
                prev = {}
        await session.execute(
            update(User)
            .where(User.id == user_id, User.deleted_time.is_(None))
            .values(notify_meta=json.dumps({**prev, **merged}, separators=(",", ":")))
        )
        await session.commit()


async def upsert_attachment_by_token(data: dict[str, Any]) -> None:
    async with db_engine.session() as session:
        existing = (
            await session.execute(
                select(Attachments).where(
                    Attachments.token == data["token"], Attachments.deleted_time.is_(None)
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(Attachments(id=cuid(), **data))
        else:
            for key, value in data.items():
                setattr(existing, key, value)
        await session.commit()


async def update_attachment_hash_by_token(token: str, hash_: str) -> None:
    async with db_engine.session() as session:
        await session.execute(
            update(Attachments)
            .where(Attachments.token == token, Attachments.deleted_time.is_(None))
            .values(hash=hash_)
        )
        await session.commit()


# ---------------------------------------------------------------------------
# user_last_visit
# ---------------------------------------------------------------------------


async def find_last_visits(
    user_id: str,
    *,
    resource_types: list[str] | None = None,
    parent_resource_id: str | None = None,
    parent_resource_ids: list[str] | None = None,
    order_desc: bool = True,
) -> list[dict[str, Any]]:
    async with db_engine.session() as session:
        stmt = select(UserLastVisit).where(UserLastVisit.user_id == user_id)
        if resource_types is not None:
            stmt = stmt.where(UserLastVisit.resource_type.in_(resource_types))
        if parent_resource_id is not None:
            stmt = stmt.where(UserLastVisit.parent_resource_id == parent_resource_id)
        if parent_resource_ids is not None:
            stmt = stmt.where(UserLastVisit.parent_resource_id.in_(parent_resource_ids))
        if order_desc:
            stmt = stmt.order_by(UserLastVisit.last_visit_time.desc())
        rows = (await session.execute(stmt)).scalars().all()
    return [
        {c.name: getattr(row, c.name) for c in UserLastVisit.__table__.columns} for row in rows
    ]


async def upsert_last_visit(
    user_id: str, resource_type: str, resource_id: str, parent_resource_id: str
) -> None:
    now = datetime.now(tz=UTC).replace(tzinfo=None)
    async with db_engine.session() as session:
        existing = (
            await session.execute(
                select(UserLastVisit).where(
                    UserLastVisit.user_id == user_id,
                    UserLastVisit.resource_type == resource_type,
                    UserLastVisit.resource_id == resource_id,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                UserLastVisit(
                    id=cuid(),
                    user_id=user_id,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    parent_resource_id=parent_resource_id,
                    last_visit_time=now,
                )
            )
        else:
            existing.last_visit_time = now
        await session.commit()


async def prune_last_visits(
    user_id: str, resource_type: str, parent_resource_id: str, keep: int
) -> None:
    """Keep only the newest ``keep`` rows per (user, type, parent)."""
    async with db_engine.session() as session:
        rows = (
            (
                await session.execute(
                    select(UserLastVisit.id)
                    .where(
                        UserLastVisit.user_id == user_id,
                        UserLastVisit.resource_type == resource_type,
                        UserLastVisit.parent_resource_id == parent_resource_id,
                    )
                    .order_by(UserLastVisit.last_visit_time.desc())
                    .offset(keep)
                )
            )
            .scalars()
            .all()
        )
        if rows:
            await session.execute(delete(UserLastVisit).where(UserLastVisit.id.in_(rows)))
        await session.commit()


async def delete_last_visits_for_user(user_id: str) -> None:
    async with db_engine.session() as session:
        await session.execute(delete(UserLastVisit).where(UserLastVisit.user_id == user_id))
        await session.commit()
