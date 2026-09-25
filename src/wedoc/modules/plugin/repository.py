"""Plugin persistence — developer-center CRUD + auth lookups."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, or_, select

from ...core.errors import ApiError, HttpErrorCode
from ...db import engine as db_engine
from ...db.models_meta import AccessToken, Plugin, PluginInstall, User

_NOT_FOUND = {"localization": {"i18nKey": "httpErrors.plugin.notFound"}}

_SELECT = (
    Plugin.id,
    Plugin.name,
    Plugin.description,
    Plugin.detail_desc,
    Plugin.positions,
    Plugin.help_url,
    Plugin.logo,
    Plugin.url,
    Plugin.status,
    Plugin.config,
    Plugin.i18n,
    Plugin.masked_secret,
    Plugin.plugin_user,
    Plugin.created_by,
    Plugin.created_time,
    Plugin.last_modified_time,
)


def not_found() -> ApiError:
    return ApiError("Plugin not found", HttpErrorCode.NOT_FOUND, _NOT_FOUND)


def _row(m: Any) -> dict[str, Any]:
    return {
        "id": m["id"],
        "name": m["name"],
        "description": m["description"],
        "detailDesc": m["detail_desc"],
        "positions": m["positions"],
        "helpUrl": m["help_url"],
        "logo": m["logo"],
        "url": m["url"],
        "status": m["status"],
        "config": m["config"],
        "i18n": m["i18n"],
        "maskedSecret": m["masked_secret"],
        "pluginUser": m["plugin_user"],
        "createdBy": m["created_by"],
        "createdTime": m["created_time"],
        "lastModifiedTime": m["last_modified_time"],
    }


def _manageable_where(user_id: str, is_admin: bool) -> Any:
    if is_admin:
        return Plugin.created_by.in_(["system", user_id])
    return Plugin.created_by == user_id


async def create_plugin(values: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(UTC).replace(tzinfo=None)
    values = {**values, "created_time": now, "last_modified_time": now}
    async with db_engine.session() as session:
        await session.execute(Plugin.__table__.insert().values(**values))
        await session.commit()
        row = (
            await session.execute(select(*_SELECT).where(Plugin.id == values["id"]))
        ).mappings().one()
    return _row(row)


async def get_plugin(plugin_id: str, user_id: str, is_admin: bool) -> dict[str, Any]:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(*_SELECT).where(
                    Plugin.id == plugin_id, _manageable_where(user_id, is_admin)
                )
            )
        ).mappings().one_or_none()
    if row is None:
        raise not_found()
    return _row(row)


async def list_plugins(user_id: str, is_admin: bool) -> list[dict[str, Any]]:
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(*_SELECT).where(_manageable_where(user_id, is_admin))
            )
        ).mappings().all()
    return [_row(r) for r in rows]


async def update_plugin(
    plugin_id: str, user_id: str, is_admin: bool, values: dict[str, Any]
) -> dict[str, Any]:
    async with db_engine.session() as session:
        result = await session.execute(
            Plugin.__table__.update()
            .where(Plugin.id == plugin_id, _manageable_where(user_id, is_admin))
            .values(**values)
        )
        if result.rowcount == 0:
            raise not_found()
        await session.commit()
        row = (
            await session.execute(select(*_SELECT).where(Plugin.id == plugin_id))
        ).mappings().one()
    return _row(row)


async def delete_plugin(plugin_id: str, user_id: str) -> str | None:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(Plugin.plugin_user).where(
                    Plugin.id == plugin_id, Plugin.created_by == user_id
                )
            )
        ).first()
        if row is None:
            raise not_found()
        await session.execute(delete(Plugin).where(Plugin.id == plugin_id))
        if row[0]:
            await session.execute(delete(User).where(User.id == row[0]))
        await session.commit()
    return row[0]


async def regenerate_secret(
    plugin_id: str, user_id: str, hashed: str, masked: str
) -> None:
    async with db_engine.session() as session:
        result = await session.execute(
            Plugin.__table__.update()
            .where(Plugin.id == plugin_id, Plugin.created_by == user_id)
            .values(secret=hashed, masked_secret=masked)
        )
        if result.rowcount == 0:
            raise not_found()
        await session.commit()


async def submit_plugin(plugin_id: str, user_id: str) -> None:
    async with db_engine.session() as session:
        await session.execute(
            Plugin.__table__.update()
            .where(Plugin.id == plugin_id, Plugin.created_by == user_id)
            .values(status="reviewing")
        )
        await session.commit()


async def unpublish_plugin(plugin_id: str, user_id: str) -> None:
    async with db_engine.session() as session:
        result = await session.execute(
            Plugin.__table__.update()
            .where(
                Plugin.id == plugin_id,
                Plugin.created_by == user_id,
                Plugin.status == "published",
            )
            .values(status="developing")
        )
        if result.rowcount == 0:
            raise not_found()
        await session.commit()


async def center_list(
    user_id: str, positions: list[str] | None, ids: list[str] | None
) -> list[dict[str, Any]]:
    async with db_engine.session() as session:
        stmt = select(
            Plugin.id,
            Plugin.name,
            Plugin.description,
            Plugin.detail_desc,
            Plugin.logo,
            Plugin.status,
            Plugin.url,
            Plugin.help_url,
            Plugin.i18n,
            Plugin.created_time,
            Plugin.last_modified_time,
            Plugin.created_by,
        )
        if ids:
            stmt = stmt.where(Plugin.id.in_(ids))
        stmt = stmt.where(
            or_(
                Plugin.status == "published",
                (Plugin.status != "published") & (Plugin.created_by == user_id),
            )
        )
        if positions:
            stmt = stmt.where(
                or_(*[Plugin.positions.contains(f'"{p}"') for p in positions])
            )
        rows = (await session.execute(stmt)).mappings().all()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "description": r["description"],
            "detailDesc": r["detail_desc"],
            "logo": r["logo"],
            "status": r["status"],
            "url": r["url"],
            "helpUrl": r["help_url"],
            "i18n": r["i18n"],
            "createdTime": r["created_time"],
            "lastModifiedTime": r["last_modified_time"],
            "createdBy": r["created_by"],
        }
        for r in rows
    ]


async def get_users(user_ids: list[str]) -> dict[str, dict[str, Any]]:
    ids = [u for u in set(user_ids) if u and u != "system"]
    result: dict[str, dict[str, Any]] = {}
    if ids:
        async with db_engine.session() as session:
            rows = (
                await session.execute(
                    select(User.id, User.name, User.email, User.avatar).where(User.id.in_(ids))
                )
            ).all()
        for r in rows:
            result[r[0]] = {"id": r[0], "name": r[1], "email": r[2], "avatar": r[3]}
    return result


# --- auth lookups ----------------------------------------------------------


async def validate_secret_row(plugin_id: str, user_id: str) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(
                    Plugin.id, Plugin.name, Plugin.secret, Plugin.plugin_user
                ).where(
                    Plugin.id == plugin_id,
                    or_(
                        Plugin.status == "published",
                        (Plugin.status != "published") & (Plugin.created_by == user_id),
                    ),
                )
            )
        ).first()
    if row is None:
        return None
    return {"id": row[0], "name": row[1], "secret": row[2], "pluginUser": row[3]}


async def active_installs(plugin_id: str, base_id: str) -> list[dict[str, Any]]:
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(
                    PluginInstall.id, PluginInstall.position, PluginInstall.position_id
                ).where(PluginInstall.plugin_id == plugin_id, PluginInstall.base_id == base_id)
            )
        ).all()
    return [{"id": r[0], "position": r[1], "positionId": r[2]} for r in rows]


async def refresh_token_source(token_id: str) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(AccessToken.id, AccessToken.base_ids, AccessToken.scopes).where(
                    AccessToken.id == token_id
                )
            )
        ).first()
    if row is None:
        return None
    return {"id": row[0], "baseIds": row[1], "scopes": row[2]}
