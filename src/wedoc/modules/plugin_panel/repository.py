"""Plugin-panel persistence."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select

from ...core.errors import ApiError, HttpErrorCode
from ...db import engine as db_engine
from ...db.models_meta import Plugin, PluginInstall, PluginPanel, TableMeta

_PANEL_NOT_FOUND = {"localization": {"i18nKey": "httpErrors.pluginPanel.notFound"}}
_TABLE_NOT_FOUND = {"localization": {"i18nKey": "httpErrors.table.notFound"}}
_PLUGIN_NOT_FOUND = {"localization": {"i18nKey": "httpErrors.plugin.notFound"}}
_INSTALL_NOT_FOUND = {"localization": {"i18nKey": "httpErrors.pluginInstall.notFound"}}
_POS = "panel"


def panel_not_found() -> ApiError:
    return ApiError("Plugin panel not found", HttpErrorCode.VALIDATION_ERROR, _PANEL_NOT_FOUND)


def install_not_found() -> ApiError:
    return ApiError("Plugin install not found", HttpErrorCode.VALIDATION_ERROR, _INSTALL_NOT_FOUND)


def _server_error() -> ApiError:
    return ApiError("Internal Server Error", HttpErrorCode.INTERNAL_SERVER_ERROR)


async def get_base_id(table_id: str) -> str:
    async with db_engine.session() as session:
        row = (
            await session.execute(select(TableMeta.base_id).where(TableMeta.id == table_id))
        ).first()
    if row is None:
        raise ApiError("Table not found", HttpErrorCode.VALIDATION_ERROR, _TABLE_NOT_FOUND)
    return row[0]


async def create_panel(panel_id: str, name: str, table_id: str, user_id: str) -> dict[str, Any]:
    async with db_engine.session() as session:
        await session.execute(
            PluginPanel.__table__.insert().values(
                id=panel_id, name=name, table_id=table_id, created_by=user_id
            )
        )
        await session.commit()
    return {"id": panel_id, "name": name}


async def list_panels(table_id: str) -> list[dict[str, Any]]:
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(PluginPanel.id, PluginPanel.name).where(PluginPanel.table_id == table_id)
            )
        ).all()
    return [{"id": r[0], "name": r[1]} for r in rows]


async def get_panel(table_id: str, panel_id: str) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(PluginPanel.id, PluginPanel.name, PluginPanel.layout).where(
                    PluginPanel.id == panel_id, PluginPanel.table_id == table_id
                )
            )
        ).first()
    return {"id": row[0], "name": row[1], "layout": row[2]} if row else None


async def get_layout(table_id: str, panel_id: str) -> tuple[bool, str | None]:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(PluginPanel.layout).where(
                    PluginPanel.id == panel_id, PluginPanel.table_id == table_id
                )
            )
        ).first()
    if row is None:
        return False, None
    return True, row[0]


async def panel_installs(panel_id: str) -> list[dict[str, Any]]:
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(
                    PluginInstall.id,
                    PluginInstall.name,
                    PluginInstall.plugin_id,
                    PluginInstall.position_id,
                    Plugin.url,
                )
                .join(Plugin, Plugin.id == PluginInstall.plugin_id, isouter=True)
                .where(PluginInstall.position == _POS, PluginInstall.position_id == panel_id)
            )
        ).all()
    return [
        {"id": r[0], "name": r[1], "pluginId": r[2], "positionId": r[3], "url": r[4]} for r in rows
    ]


async def rename_panel(table_id: str, panel_id: str, name: str, user_id: str) -> dict[str, Any]:
    async with db_engine.session() as session:
        result = await session.execute(
            PluginPanel.__table__.update()
            .where(PluginPanel.id == panel_id, PluginPanel.table_id == table_id)
            .values(name=name, last_modified_by=user_id)
        )
        if result.rowcount == 0:
            raise _server_error()
        await session.commit()
    return {"id": panel_id, "name": name}


async def delete_panel(table_id: str, panel_id: str) -> None:
    async with db_engine.session() as session:
        result = await session.execute(
            delete(PluginPanel).where(
                PluginPanel.id == panel_id, PluginPanel.table_id == table_id
            )
        )
        if result.rowcount == 0:
            raise _server_error()
        await session.commit()


async def update_layout(
    table_id: str, panel_id: str, layout_json: str, user_id: str
) -> dict[str, Any]:
    async with db_engine.session() as session:
        result = await session.execute(
            PluginPanel.__table__.update()
            .where(PluginPanel.id == panel_id, PluginPanel.table_id == table_id)
            .values(layout=layout_json, last_modified_by=user_id)
        )
        if result.rowcount == 0:
            raise _server_error()
        row = (
            await session.execute(
                select(PluginPanel.id, PluginPanel.layout).where(PluginPanel.id == panel_id)
            )
        ).first()
        await session.commit()
    return {"id": row[0], "layout": row[1]}


async def set_layout(panel_id: str, layout_json: str) -> None:
    async with db_engine.session() as session:
        await session.execute(
            PluginPanel.__table__.update()
            .where(PluginPanel.id == panel_id)
            .values(layout=layout_json)
        )
        await session.commit()


async def get_plugin_user(plugin_id: str) -> tuple[bool, str | None]:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(Plugin.id, Plugin.plugin_user).where(Plugin.id == plugin_id)
            )
        ).first()
    if row is None:
        return False, None
    return True, row[1]


async def create_install(values: dict[str, Any]) -> None:
    async with db_engine.session() as session:
        await session.execute(PluginInstall.__table__.insert().values(**values))
        await session.commit()


async def delete_install(base_id: str, panel_id: str, plugin_install_id: str) -> None:
    async with db_engine.session() as session:
        result = await session.execute(
            delete(PluginInstall).where(
                PluginInstall.id == plugin_install_id,
                PluginInstall.position_id == panel_id,
                PluginInstall.base_id == base_id,
            )
        )
        if result.rowcount == 0:
            raise _server_error()
        await session.commit()


async def rename_install(
    base_id: str, panel_id: str, plugin_install_id: str, name: str, user_id: str
) -> None:
    async with db_engine.session() as session:
        result = await session.execute(
            PluginInstall.__table__.update()
            .where(
                PluginInstall.id == plugin_install_id,
                PluginInstall.position_id == panel_id,
                PluginInstall.base_id == base_id,
            )
            .values(name=name, last_modified_by=user_id)
        )
        if result.rowcount == 0:
            raise _server_error()
        await session.commit()


async def update_install_storage(
    base_id: str, panel_id: str, plugin_install_id: str, storage_json: str | None, user_id: str
) -> str | None:
    async with db_engine.session() as session:
        result = await session.execute(
            PluginInstall.__table__.update()
            .where(
                PluginInstall.id == plugin_install_id,
                PluginInstall.position_id == panel_id,
                PluginInstall.base_id == base_id,
            )
            .values(storage=storage_json, last_modified_by=user_id)
        )
        if result.rowcount == 0:
            raise _server_error()
        row = (
            await session.execute(
                select(PluginInstall.storage).where(PluginInstall.id == plugin_install_id)
            )
        ).first()
        await session.commit()
    return row[0]


async def get_install(base_id: str, panel_id: str, plugin_install_id: str) -> dict[str, Any]:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(
                    PluginInstall.id,
                    PluginInstall.name,
                    PluginInstall.plugin_id,
                    PluginInstall.storage,
                ).where(
                    PluginInstall.id == plugin_install_id,
                    PluginInstall.position_id == panel_id,
                    PluginInstall.base_id == base_id,
                )
            )
        ).first()
    if row is None:
        raise install_not_found()
    return {"id": row[0], "name": row[1], "pluginId": row[2], "storage": row[3]}


async def panel_names(table_id: str) -> list[str]:
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(PluginPanel.name).where(PluginPanel.table_id == table_id)
            )
        ).all()
    return [r[0] for r in rows]


async def install_names(base_id: str, panel_id: str) -> list[str]:
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(PluginInstall.name).where(
                    PluginInstall.base_id == base_id,
                    PluginInstall.position_id == panel_id,
                    PluginInstall.position == _POS,
                )
            )
        ).all()
    return [r[0] for r in rows]


async def installs_for_duplicate(panel_id: str) -> list[dict[str, Any]]:
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(
                    PluginInstall.id,
                    PluginInstall.name,
                    PluginInstall.plugin_id,
                    PluginInstall.storage,
                    PluginInstall.base_id,
                ).where(
                    PluginInstall.position_id == panel_id, PluginInstall.position == _POS
                )
            )
        ).all()
    return [
        {"id": r[0], "name": r[1], "pluginId": r[2], "storage": r[3], "baseId": r[4]} for r in rows
    ]


async def clone_install(
    base_id: str,
    panel_id: str,
    plugin_id: str,
    name: str,
    storage: str | None,
    new_id: str,
    user_id: str,
) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    async with db_engine.session() as session:
        await session.execute(
            PluginInstall.__table__.insert().values(
                id=new_id,
                plugin_id=plugin_id,
                base_id=base_id,
                name=name,
                position_id=panel_id,
                position=_POS,
                storage=storage,
                created_by=user_id,
                created_time=now,
            )
        )
        await session.commit()


async def find_install_for_duplicate(
    base_id: str, panel_id: str, plugin_install_id: str
) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(
                    PluginInstall.name, PluginInstall.plugin_id, PluginInstall.storage
                ).where(
                    PluginInstall.base_id == base_id,
                    PluginInstall.id == plugin_install_id,
                    PluginInstall.position_id == panel_id,
                    PluginInstall.position == _POS,
                )
            )
        ).first()
    return {"name": row[0], "pluginId": row[1], "storage": row[2]} if row else None
