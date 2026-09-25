"""Plugin-context-menu persistence."""

from typing import Any

from sqlalchemy import delete, func, select

from ...core.errors import ApiError, HttpErrorCode
from ...db import engine as db_engine
from ...db.models_meta import Plugin, PluginContextMenu, PluginInstall, TableMeta

_POS = "contextMenu"
_INSTALL_NOT_FOUND = {"localization": {"i18nKey": "httpErrors.pluginInstall.notFound"}}


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
        raise ApiError(
            "Table not found",
            HttpErrorCode.VALIDATION_ERROR,
            {"localization": {"i18nKey": "httpErrors.table.notFound"}},
        )
    return row[0]


async def plugin_name(plugin_id: str) -> str | None:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(Plugin.name, Plugin.plugin_user).where(Plugin.id == plugin_id)
            )
        ).first()
    return None if row is None else row[0]


async def plugin_name_and_user(plugin_id: str) -> tuple[bool, str | None, str | None]:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(Plugin.name, Plugin.plugin_user).where(Plugin.id == plugin_id)
            )
        ).first()
    if row is None:
        return False, None, None
    return True, row[0], row[1]


async def max_order(table_id: str) -> float:
    async with db_engine.session() as session:
        value = (
            await session.execute(
                select(func.max(PluginContextMenu.order)).where(
                    PluginContextMenu.table_id == table_id
                )
            )
        ).scalar()
    return value or 0


async def create_install(values: dict[str, Any]) -> None:
    async with db_engine.session() as session:
        await session.execute(PluginInstall.__table__.insert().values(**values))
        await session.commit()


async def create_menu(
    menu_id: str, table_id: str, plugin_install_id: str, order: float, user_id: str
) -> None:
    async with db_engine.session() as session:
        await session.execute(
            PluginContextMenu.__table__.insert().values(
                id=menu_id,
                table_id=table_id,
                plugin_install_id=plugin_install_id,
                order=order,
                created_by=user_id,
            )
        )
        await session.commit()


async def list_menu(table_id: str) -> list[dict[str, Any]]:
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(
                    PluginContextMenu.plugin_install_id,
                    PluginContextMenu.order,
                    PluginInstall.name,
                    PluginInstall.plugin_id,
                    Plugin.logo,
                )
                .join(
                    PluginInstall,
                    PluginInstall.id == PluginContextMenu.plugin_install_id,
                    isouter=True,
                )
                .join(Plugin, Plugin.id == PluginInstall.plugin_id, isouter=True)
                .where(PluginContextMenu.table_id == table_id)
                .order_by(PluginContextMenu.order.asc())
            )
        ).all()
    return [
        {"pluginInstallId": r[0], "order": r[1], "name": r[2], "pluginId": r[3], "logo": r[4]}
        for r in rows
        if r[2] is not None
    ]


async def get_install(base_id: str, table_id: str, plugin_install_id: str) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(
                    PluginInstall.id,
                    PluginInstall.name,
                    PluginInstall.plugin_id,
                    PluginInstall.storage,
                    PluginInstall.position_id,
                    Plugin.url,
                    Plugin.config,
                )
                .join(Plugin, Plugin.id == PluginInstall.plugin_id, isouter=True)
                .where(
                    PluginInstall.id == plugin_install_id,
                    PluginInstall.base_id == base_id,
                    PluginInstall.position_id == table_id,
                    PluginInstall.position == _POS,
                )
            )
        ).first()
    if row is None:
        return None
    return {
        "id": row[0],
        "name": row[1],
        "pluginId": row[2],
        "storage": row[3],
        "positionId": row[4],
        "url": row[5],
        "config": row[6],
    }


async def rename_install(base_id: str, table_id: str, plugin_install_id: str, name: str) -> None:
    async with db_engine.session() as session:
        result = await session.execute(
            PluginInstall.__table__.update()
            .where(
                PluginInstall.id == plugin_install_id,
                PluginInstall.base_id == base_id,
                PluginInstall.position_id == table_id,
                PluginInstall.position == _POS,
            )
            .values(name=name)
        )
        if result.rowcount == 0:
            raise _server_error()
        await session.commit()


async def update_storage(
    base_id: str, table_id: str, plugin_install_id: str, storage_json: str | None
) -> str | None:
    async with db_engine.session() as session:
        result = await session.execute(
            PluginInstall.__table__.update()
            .where(
                PluginInstall.id == plugin_install_id,
                PluginInstall.base_id == base_id,
                PluginInstall.position_id == table_id,
                PluginInstall.position == _POS,
            )
            .values(storage=storage_json)
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


async def delete_menu_and_install(base_id: str, table_id: str, plugin_install_id: str) -> None:
    async with db_engine.session() as session:
        await session.execute(
            delete(PluginContextMenu).where(
                PluginContextMenu.plugin_install_id == plugin_install_id,
                PluginContextMenu.table_id == table_id,
            )
        )
        result = await session.execute(
            delete(PluginInstall).where(
                PluginInstall.id == plugin_install_id,
                PluginInstall.base_id == base_id,
                PluginInstall.position_id == table_id,
                PluginInstall.position == _POS,
            )
        )
        if result.rowcount == 0:
            raise _server_error()
        await session.commit()


async def find_menu(table_id: str, plugin_install_id: str) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(PluginContextMenu.plugin_install_id, PluginContextMenu.order).where(
                    PluginContextMenu.plugin_install_id == plugin_install_id,
                    PluginContextMenu.table_id == table_id,
                )
            )
        ).first()
    return {"id": row[0], "order": row[1]} if row else None


async def get_next_menu(
    table_id: str, where_order: float, op: str, align: str
) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        stmt = select(PluginContextMenu.plugin_install_id, PluginContextMenu.order).where(
            PluginContextMenu.table_id == table_id
        )
        stmt = stmt.where(
            PluginContextMenu.order < where_order
            if op == "lt"
            else PluginContextMenu.order > where_order
        )
        stmt = stmt.order_by(
            PluginContextMenu.order.desc() if align == "desc" else PluginContextMenu.order.asc()
        )
        row = (await session.execute(stmt)).first()
    return {"id": row[0], "order": row[1]} if row else None


async def update_menu_order(table_id: str, plugin_install_id: str, new_order: float) -> None:
    async with db_engine.session() as session:
        await session.execute(
            PluginContextMenu.__table__.update()
            .where(
                PluginContextMenu.plugin_install_id == plugin_install_id,
                PluginContextMenu.table_id == table_id,
            )
            .values(order=new_order)
        )
        await session.commit()


async def shuffle_menu(table_id: str, anchor_order: float, op: str) -> None:
    async with db_engine.session() as session:
        await session.execute(
            PluginContextMenu.__table__.update()
            .where(
                PluginContextMenu.table_id == table_id,
                PluginContextMenu.order >= anchor_order
                if op == "gte"
                else PluginContextMenu.order > anchor_order,
            )
            .values(order=PluginContextMenu.order + 1)
        )
        await session.commit()
