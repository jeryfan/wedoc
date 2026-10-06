"""Dashboard + plugin-install persistence."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select

from ...core.errors import ApiError, HttpErrorCode
from ...db import engine as db_engine
from ...db.models_meta import Collaborator, Dashboard, Plugin, PluginInstall

_DASHBOARD_NOT_FOUND = {"localization": {"i18nKey": "httpErrors.dashboard.notFound"}}
_PLUGIN_NOT_FOUND = {"localization": {"i18nKey": "httpErrors.plugin.notFound"}}
_PLUGIN_POSITION_DASHBOARD = "dashboard"


def _not_found_dashboard() -> ApiError:
    return ApiError("Dashboard not found", HttpErrorCode.NOT_FOUND, _DASHBOARD_NOT_FOUND)


def _not_found_plugin() -> ApiError:
    return ApiError("Plugin not found", HttpErrorCode.NOT_FOUND, _PLUGIN_NOT_FOUND)


def _server_error() -> ApiError:
    return ApiError("Internal Server Error", HttpErrorCode.INTERNAL_SERVER_ERROR)


async def list_dashboards(base_id: str) -> list[dict[str, Any]]:
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(Dashboard.id, Dashboard.name)
                .where(Dashboard.base_id == base_id)
                .order_by(Dashboard.created_time.asc())
            )
        ).all()
    return [{"id": r[0], "name": r[1]} for r in rows]


async def get_dashboard(base_id: str, dashboard_id: str) -> dict[str, Any]:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(Dashboard.id, Dashboard.name, Dashboard.layout).where(
                    Dashboard.id == dashboard_id, Dashboard.base_id == base_id
                )
            )
        ).first()
    if row is None:
        raise _not_found_dashboard()
    return {"id": row[0], "name": row[1], "layout": row[2]}


async def get_dashboard_or_500(base_id: str, dashboard_id: str) -> dict[str, Any]:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(Dashboard.id, Dashboard.name, Dashboard.layout).where(
                    Dashboard.id == dashboard_id, Dashboard.base_id == base_id
                )
            )
        ).first()
    if row is None:
        raise _server_error()
    return {"id": row[0], "name": row[1], "layout": row[2]}


async def dashboard_exists(base_id: str, dashboard_id: str) -> None:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(Dashboard.id).where(
                    Dashboard.id == dashboard_id, Dashboard.base_id == base_id
                )
            )
        ).first()
    if row is None:
        raise _not_found_dashboard()


async def get_layout(base_id: str, dashboard_id: str) -> str | None:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(Dashboard.layout).where(
                    Dashboard.id == dashboard_id, Dashboard.base_id == base_id
                )
            )
        ).first()
    if row is None:
        raise _not_found_dashboard()
    return row[0]


async def get_layout_or_500(base_id: str, dashboard_id: str) -> str | None:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(Dashboard.layout).where(
                    Dashboard.id == dashboard_id, Dashboard.base_id == base_id
                )
            )
        ).first()
    if row is None:
        raise _server_error()
    return row[0]


async def create_dashboard(dashboard_id: str, base_id: str, name: str, user_id: str) -> None:
    async with db_engine.session() as session:
        await session.execute(
            Dashboard.__table__.insert().values(
                id=dashboard_id,
                base_id=base_id,
                name=name,
                created_by=user_id,
                # prisma @updatedAt also stamps lastModifiedTime on INSERT (with
                # lastModifiedBy left null), which the base-node resource meta shows.
                last_modified_time=datetime.now(UTC).replace(tzinfo=None),
            )
        )
        await session.commit()


async def rename_dashboard(base_id: str, dashboard_id: str, name: str) -> dict[str, Any]:
    async with db_engine.session() as session:
        result = await session.execute(
            Dashboard.__table__.update()
            .where(Dashboard.id == dashboard_id, Dashboard.base_id == base_id)
            .values(name=name)
        )
        if result.rowcount == 0:
            raise _not_found_dashboard()
        await session.commit()
    return {"id": dashboard_id, "name": name}


async def update_layout(base_id: str, dashboard_id: str, layout_json: str) -> dict[str, Any]:
    async with db_engine.session() as session:
        result = await session.execute(
            Dashboard.__table__.update()
            .where(Dashboard.id == dashboard_id, Dashboard.base_id == base_id)
            .values(layout=layout_json)
        )
        if result.rowcount == 0:
            raise _not_found_dashboard()
        row = (
            await session.execute(
                select(Dashboard.id, Dashboard.name, Dashboard.layout).where(
                    Dashboard.id == dashboard_id
                )
            )
        ).first()
        await session.commit()
    return {"id": row[0], "name": row[1], "layout": row[2]}


async def set_layout(dashboard_id: str, layout_json: str) -> None:
    async with db_engine.session() as session:
        await session.execute(
            Dashboard.__table__.update()
            .where(Dashboard.id == dashboard_id)
            .values(layout=layout_json)
        )
        await session.commit()


async def delete_dashboard(base_id: str, dashboard_id: str) -> None:
    async with db_engine.session() as session:
        result = await session.execute(
            delete(Dashboard).where(
                Dashboard.id == dashboard_id, Dashboard.base_id == base_id
            )
        )
        if result.rowcount == 0:
            raise _not_found_dashboard()
        await session.commit()


async def dashboard_names(base_id: str) -> list[str]:
    async with db_engine.session() as session:
        rows = (
            await session.execute(select(Dashboard.name).where(Dashboard.base_id == base_id))
        ).all()
    return [r[0] for r in rows]


# --- plugin install --------------------------------------------------------


async def validate_plugin_published(plugin_id: str, user_id: str) -> dict[str, Any]:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(Plugin.id, Plugin.plugin_user, Plugin.url).where(
                    Plugin.id == plugin_id,
                    (Plugin.status == "published")
                    | ((Plugin.status != "published") & (Plugin.created_by == user_id)),
                )
            )
        ).first()
    if row is None:
        raise _not_found_plugin()
    return {"id": row[0], "pluginUser": row[1], "url": row[2]}


async def get_install_or_plugin_404(
    base_id: str, dashboard_id: str, plugin_install_id: str, user_id: str
) -> dict[str, Any]:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(
                    PluginInstall.id,
                    PluginInstall.name,
                    PluginInstall.base_id,
                    PluginInstall.plugin_id,
                    PluginInstall.storage,
                    PluginInstall.position,
                    PluginInstall.position_id,
                    PluginInstall.created_by,
                )
                .join(Plugin, Plugin.id == PluginInstall.plugin_id)
                .where(
                    PluginInstall.id == plugin_install_id,
                    PluginInstall.base_id == base_id,
                    PluginInstall.position_id == dashboard_id,
                    PluginInstall.position == _PLUGIN_POSITION_DASHBOARD,
                    (Plugin.status == "published")
                    | ((Plugin.status != "published") & (Plugin.created_by == user_id)),
                )
            )
        ).first()
    if row is None:
        raise _not_found_plugin()
    return {
        "id": row[0],
        "name": row[1],
        "baseId": row[2],
        "pluginId": row[3],
        "storage": row[4],
        "position": row[5],
        "positionId": row[6],
        "createdBy": row[7],
    }


async def install_plugin_row(values: dict[str, Any]) -> dict[str, Any]:
    async with db_engine.session() as session:
        await session.execute(PluginInstall.__table__.insert().values(**values))
        await session.commit()
        row = (
            await session.execute(
                select(PluginInstall.id, PluginInstall.plugin_id, PluginInstall.name)
                .join(Plugin, Plugin.id == PluginInstall.plugin_id, isouter=True)
                .where(PluginInstall.id == values["id"])
            )
        ).first()
    return {"id": row[0], "pluginId": row[1], "name": row[2]}


async def plugin_user_id(plugin_id: str) -> str | None:
    async with db_engine.session() as session:
        row = (
            await session.execute(select(Plugin.plugin_user).where(Plugin.id == plugin_id))
        ).first()
    return row[0] if row else None


async def base_collaborator_count(principal_id: str, base_id: str) -> int:
    async with db_engine.session() as session:
        return (
            await session.execute(
                select(func.count())
                .select_from(Collaborator)
                .where(
                    Collaborator.principal_id == principal_id,
                    Collaborator.principal_type == "user",
                    Collaborator.resource_id == base_id,
                    Collaborator.resource_type == "base",
                )
            )
        ).scalar_one()


async def delete_install(
    base_id: str, dashboard_id: str, plugin_install_id: str, user_id: str
) -> None:
    async with db_engine.session() as session:
        # mirror the prisma delete guarded by plugin publish/ownership
        row = (
            await session.execute(
                select(PluginInstall.id)
                .join(Plugin, Plugin.id == PluginInstall.plugin_id)
                .where(
                    PluginInstall.id == plugin_install_id,
                    PluginInstall.base_id == base_id,
                    PluginInstall.position_id == dashboard_id,
                    PluginInstall.position == _PLUGIN_POSITION_DASHBOARD,
                    (Plugin.status == "published")
                    | ((Plugin.status != "published") & (Plugin.created_by == user_id)),
                )
            )
        ).first()
        if row is None:
            raise _not_found_plugin()
        await session.execute(delete(PluginInstall).where(PluginInstall.id == plugin_install_id))
        await session.commit()


async def rename_install(plugin_install_id: str, name: str) -> None:
    async with db_engine.session() as session:
        await session.execute(
            PluginInstall.__table__.update()
            .where(PluginInstall.id == plugin_install_id)
            .values(name=name)
        )
        await session.commit()


async def update_install_storage(plugin_install_id: str, storage_json: str | None) -> str | None:
    async with db_engine.session() as session:
        await session.execute(
            PluginInstall.__table__.update()
            .where(PluginInstall.id == plugin_install_id)
            .values(storage=storage_json)
        )
        row = (
            await session.execute(
                select(PluginInstall.storage).where(PluginInstall.id == plugin_install_id)
            )
        ).first()
        await session.commit()
    return row[0]


async def install_names(base_id: str, dashboard_id: str) -> list[str]:
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(PluginInstall.name).where(
                    PluginInstall.base_id == base_id,
                    PluginInstall.position_id == dashboard_id,
                    PluginInstall.position == _PLUGIN_POSITION_DASHBOARD,
                )
            )
        ).all()
    return [r[0] for r in rows]


async def clone_install(source: dict[str, Any], new_id: str, new_name: str, user_id: str) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    async with db_engine.session() as session:
        await session.execute(
            PluginInstall.__table__.insert().values(
                id=new_id,
                plugin_id=source["pluginId"],
                base_id=source["baseId"],
                name=new_name,
                position_id=source["positionId"],
                position=source["position"],
                storage=source["storage"],
                created_by=user_id,
                created_time=now,
            )
        )
        await session.commit()


async def list_installs_with_url(base_id: str, dashboard_id: str) -> list[dict[str, Any]]:
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(
                    PluginInstall.id,
                    PluginInstall.name,
                    PluginInstall.plugin_id,
                    Plugin.url,
                )
                .join(Plugin, Plugin.id == PluginInstall.plugin_id, isouter=True)
                .where(
                    PluginInstall.position_id == dashboard_id,
                    PluginInstall.position == _PLUGIN_POSITION_DASHBOARD,
                )
            )
        ).all()
    return [{"id": r[0], "name": r[1], "pluginId": r[2], "url": r[3]} for r in rows]


async def list_installs_for_duplicate(base_id: str, dashboard_id: str) -> list[dict[str, Any]]:
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(
                    PluginInstall.id,
                    PluginInstall.name,
                    PluginInstall.plugin_id,
                    PluginInstall.storage,
                ).where(
                    PluginInstall.base_id == base_id,
                    PluginInstall.position_id == dashboard_id,
                    PluginInstall.position == _PLUGIN_POSITION_DASHBOARD,
                )
            )
        ).all()
    return [{"id": r[0], "name": r[1], "pluginId": r[2], "storage": r[3]} for r in rows]
