"""Access-token persistence — raw-row access, business rules live in service."""

from datetime import datetime
from typing import Any

from sqlalchemy import select

from ...core.errors import ApiError, HttpErrorCode
from ...db import engine as db_engine
from ...db.models_meta import AccessToken, Base, Space

_SELECT = (
    AccessToken.id,
    AccessToken.name,
    AccessToken.description,
    AccessToken.scopes,
    AccessToken.space_ids,
    AccessToken.base_ids,
    AccessToken.has_full_access,
    AccessToken.created_time,
    AccessToken.expired_time,
    AccessToken.last_used_time,
)


def _row(mapping: Any) -> dict[str, Any]:
    return {
        "id": mapping["id"],
        "name": mapping["name"],
        "description": mapping["description"],
        "scopes": mapping["scopes"],
        "spaceIds": mapping["space_ids"],
        "baseIds": mapping["base_ids"],
        "hasFullAccess": mapping["has_full_access"],
        "createdTime": mapping["created_time"],
        "expiredTime": mapping["expired_time"],
        "lastUsedTime": mapping["last_used_time"],
    }


async def list_by_user(user_id: str) -> list[dict[str, Any]]:
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(*_SELECT)
                .where(AccessToken.user_id == user_id, AccessToken.client_id.is_(None))
                .order_by(AccessToken.created_time.desc())
            )
        ).mappings().all()
    return [_row(r) for r in rows]


async def create(
    *,
    token_id: str,
    name: str,
    description: str | None,
    scopes: str,
    space_ids: str | None,
    base_ids: str | None,
    user_id: str,
    sign: str,
    client_id: str | None,
    expired_time: datetime,
    has_full_access: bool | None,
) -> dict[str, Any]:
    async with db_engine.session() as session:
        await session.execute(
            AccessToken.__table__.insert().values(
                id=token_id,
                name=name,
                description=description,
                scopes=scopes,
                space_ids=space_ids,
                base_ids=base_ids,
                user_id=user_id,
                sign=sign,
                client_id=client_id,
                expired_time=expired_time,
                has_full_access=has_full_access,
            )
        )
        await session.commit()
        row = (
            await session.execute(select(*_SELECT).where(AccessToken.id == token_id))
        ).mappings().one()
        return _row(row)


async def get_or_throw(user_id: str, token_id: str) -> dict[str, Any]:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(*_SELECT).where(
                    AccessToken.user_id == user_id, AccessToken.id == token_id
                )
            )
        ).mappings().one_or_none()
    if row is None:
        raise ApiError("Internal Server Error", HttpErrorCode.INTERNAL_SERVER_ERROR)
    return _row(row)


async def update(
    user_id: str,
    token_id: str,
    *,
    name: str,
    description: str | None,
    scopes: str,
    space_ids: str | None,
    base_ids: str | None,
    has_full_access: bool | None,
) -> dict[str, Any]:
    async with db_engine.session() as session:
        result = await session.execute(
            AccessToken.__table__.update()
            .where(AccessToken.id == token_id, AccessToken.user_id == user_id)
            .values(
                name=name,
                description=description,
                scopes=scopes,
                space_ids=space_ids,
                base_ids=base_ids,
                has_full_access=has_full_access,
            )
        )
        if result.rowcount == 0:
            raise ApiError("Internal Server Error", HttpErrorCode.INTERNAL_SERVER_ERROR)
        await session.commit()
        row = (
            await session.execute(
                select(
                    AccessToken.id,
                    AccessToken.name,
                    AccessToken.description,
                    AccessToken.scopes,
                    AccessToken.space_ids,
                    AccessToken.base_ids,
                    AccessToken.has_full_access,
                ).where(AccessToken.id == token_id)
            )
        ).mappings().one()
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "scopes": row["scopes"],
        "spaceIds": row["space_ids"],
        "baseIds": row["base_ids"],
        "hasFullAccess": row["has_full_access"],
    }


async def refresh(
    user_id: str, token_id: str, sign: str, expired_time: datetime | None
) -> dict[str, Any]:
    values: dict[str, Any] = {"sign": sign}
    if expired_time is not None:
        values["expired_time"] = expired_time
    async with db_engine.session() as session:
        result = await session.execute(
            AccessToken.__table__.update()
            .where(AccessToken.id == token_id, AccessToken.user_id == user_id)
            .values(**values)
        )
        if result.rowcount == 0:
            raise ApiError("Internal Server Error", HttpErrorCode.INTERNAL_SERVER_ERROR)
        await session.commit()
        row = (
            await session.execute(
                select(
                    AccessToken.id,
                    AccessToken.name,
                    AccessToken.description,
                    AccessToken.scopes,
                    AccessToken.space_ids,
                    AccessToken.base_ids,
                    AccessToken.expired_time,
                    AccessToken.last_used_time,
                ).where(AccessToken.id == token_id)
            )
        ).mappings().one()
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "scopes": row["scopes"],
        "spaceIds": row["space_ids"],
        "baseIds": row["base_ids"],
        "expiredTime": row["expired_time"],
        "lastUsedTime": row["last_used_time"],
    }


async def delete(user_id: str, token_id: str) -> None:
    async with db_engine.session() as session:
        result = await session.execute(
            AccessToken.__table__.delete().where(
                AccessToken.id == token_id, AccessToken.user_id == user_id
            )
        )
        if result.rowcount == 0:
            raise ApiError("Internal Server Error", HttpErrorCode.INTERNAL_SERVER_ERROR)
        await session.commit()


async def filter_existing_space_ids(space_ids: list[str]) -> list[str]:
    if not space_ids:
        return []
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(Space.id).where(
                    Space.id.in_(space_ids), Space.deleted_time.is_(None)
                )
            )
        ).all()
    return [r[0] for r in rows]


async def filter_existing_base_ids(base_ids: list[str]) -> list[str]:
    if not base_ids:
        return []
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(Base.id).where(Base.id.in_(base_ids), Base.deleted_time.is_(None))
            )
        ).all()
    return [r[0] for r in rows]


async def get_row(token_id: str) -> dict[str, Any] | None:
    """Raw access-token row by id (oauth refresh needs user_id + scopes)."""
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(
                    AccessToken.id,
                    AccessToken.user_id,
                    AccessToken.scopes,
                    AccessToken.client_id,
                ).where(AccessToken.id == token_id)
            )
        ).first()
    if row is None:
        return None
    return {"id": row[0], "user_id": row[1], "scopes": row[2], "client_id": row[3]}
