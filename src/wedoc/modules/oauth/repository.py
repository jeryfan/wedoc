"""OAuth client persistence."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select

from ...db import engine as db_engine
from ...db.models_meta import (
    AccessToken,
    OAuthApp,
    OAuthAppAuthorized,
    OAuthAppSecret,
    OAuthAppToken,
    User,
)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


async def create_app(values: dict[str, Any]) -> dict[str, Any]:
    async with db_engine.session() as session:
        await session.execute(OAuthApp.__table__.insert().values(**values))
        await session.commit()
        row = (
            await session.execute(
                select(OAuthApp).where(OAuthApp.client_id == values["client_id"])
            )
        ).scalar_one()
        return _app_dict(row)


def _app_dict(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "scopes": row.scopes,
        "homepage": row.homepage,
        "logo": row.logo,
        "redirectUris": row.redirect_uris,
        "allowDeviceFlow": row.allow_device_flow,
        "clientId": row.client_id,
        "createdBy": row.created_by,
    }


async def get_app(client_id: str) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        row = (
            await session.execute(select(OAuthApp).where(OAuthApp.client_id == client_id))
        ).scalar_one_or_none()
    return _app_dict(row) if row else None


async def get_created_by(client_id: str) -> str | None:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(OAuthApp.created_by).where(OAuthApp.client_id == client_id)
            )
        ).first()
    return row[0] if row else None


async def update_app(client_id: str, values: dict[str, Any]) -> dict[str, Any]:
    async with db_engine.session() as session:
        await session.execute(
            OAuthApp.__table__.update().where(OAuthApp.client_id == client_id).values(**values)
        )
        await session.commit()
        row = (
            await session.execute(select(OAuthApp).where(OAuthApp.client_id == client_id))
        ).scalar_one()
        return _app_dict(row)


async def list_apps(client_ids_where: dict[str, Any]) -> list[dict[str, Any]]:
    async with db_engine.session() as session:
        stmt = select(
            OAuthApp.client_id,
            OAuthApp.name,
            OAuthApp.logo,
            OAuthApp.homepage,
            OAuthApp.description,
        )
        creators = client_ids_where["created_by"]
        stmt = stmt.where(OAuthApp.created_by.in_(creators))
        rows = (await session.execute(stmt)).mappings().all()
    return [
        {
            "clientId": r["client_id"],
            "name": r["name"],
            "logo": r["logo"],
            "homepage": r["homepage"],
            "description": r["description"],
        }
        for r in rows
    ]


async def list_secrets(client_id: str) -> list[dict[str, Any]]:
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(
                    OAuthAppSecret.id,
                    OAuthAppSecret.masked_secret,
                    OAuthAppSecret.last_used_time,
                )
                .where(OAuthAppSecret.client_id == client_id)
                .order_by(OAuthAppSecret.created_time.desc())
            )
        ).mappings().all()
    return [
        {"id": r["id"], "secret": r["masked_secret"], "lastUsedTime": _iso(r["last_used_time"])}
        for r in rows
    ]


async def create_secret(values: dict[str, Any]) -> dict[str, Any]:
    async with db_engine.session() as session:
        await session.execute(OAuthAppSecret.__table__.insert().values(**values))
        await session.commit()
        row = (
            await session.execute(
                select(OAuthAppSecret.id, OAuthAppSecret.last_used_time).where(
                    OAuthAppSecret.id == values["id"]
                )
            )
        ).first()
    return {"id": row[0], "lastUsedTime": _iso(row[1])}


async def delete_secret(client_id: str, secret_id: str) -> int:
    async with db_engine.session() as session:
        result = await session.execute(
            delete(OAuthAppSecret).where(
                OAuthAppSecret.id == secret_id, OAuthAppSecret.client_id == client_id
            )
        )
        await session.commit()
    return result.rowcount


async def delete_app(client_id: str) -> None:
    async with db_engine.session() as session:
        await session.execute(delete(OAuthApp).where(OAuthApp.client_id == client_id))
        await session.execute(delete(AccessToken).where(AccessToken.client_id == client_id))
        await session.commit()


async def revoke_access(client_id: str) -> None:
    async with db_engine.session() as session:
        await session.execute(
            delete(OAuthAppAuthorized).where(OAuthAppAuthorized.client_id == client_id)
        )
        await session.execute(delete(OAuthAppToken).where(OAuthAppToken.client_id == client_id))
        await session.execute(delete(AccessToken).where(AccessToken.client_id == client_id))
        await session.commit()


async def revoke_token(client_id: str, user_id: str) -> int:
    async with db_engine.session() as session:
        result = await session.execute(
            delete(OAuthAppAuthorized).where(
                OAuthAppAuthorized.client_id == client_id,
                OAuthAppAuthorized.user_id == user_id,
            )
        )
        if result.rowcount == 0:
            return 0
        await session.execute(
            delete(OAuthAppToken).where(
                OAuthAppToken.client_id == client_id, OAuthAppToken.created_by == user_id
            )
        )
        await session.execute(
            delete(AccessToken).where(
                AccessToken.client_id == client_id, AccessToken.user_id == user_id
            )
        )
        await session.commit()
    return result.rowcount


async def list_authorized(user_id: str) -> list[str]:
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(OAuthAppAuthorized.client_id).where(
                    OAuthAppAuthorized.user_id == user_id
                )
            )
        ).all()
    return [r[0] for r in rows]


async def apps_by_client_ids(client_ids: list[str]) -> list[Any]:
    if not client_ids:
        return []
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(OAuthApp).where(OAuthApp.client_id.in_(client_ids))
            )
        ).scalars().all()
    return list(rows)


async def users_by_ids(user_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not user_ids:
        return {}
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(User.id, User.email, User.name).where(User.id.in_(user_ids))
            )
        ).all()
    return {r[0]: {"email": r[1], "name": r[2]} for r in rows}
