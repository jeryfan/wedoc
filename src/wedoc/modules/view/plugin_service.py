"""View plugin install/get/update — ports the view plugin endpoints.

A plugin view is a real view row (type=plugin) whose options carry
{pluginInstallId, pluginId, pluginLogo}; a plugin_install row (position=view,
positionId=viewId) holds its storage. Response shapes mirror the reference's
V2 view-plugin endpoints (which the canary runs for fresh bases).
"""

import json
from typing import Any

from sqlalchemy import select, text, update

from ...core.errors import ApiError, HttpErrorCode
from ...core.ids import IdPrefix, new_id
from ...db import engine as db_engine
from ...db.models_meta import Plugin, PluginInstall
from ..field import repository as field_repository
from .schemas import ViewCreateBody
from .service import ViewService

_POSITION_VIEW = "view"


def _plugin_not_found(plugin_id: str) -> ApiError:
    return ApiError(
        f"Plugin not found with id: {plugin_id}",
        HttpErrorCode.NOT_FOUND,
        {"domainCode": "not_found", "domainTags": ["not-found"]},
    )


def _install_not_found_for_view(view_id: str) -> ApiError:
    return ApiError(
        f"Plugin installation not found for View: {view_id}",
        HttpErrorCode.NOT_FOUND,
        {"domainCode": "not_found", "domainTags": ["not-found"]},
    )


def _install_not_found(plugin_install_id: str) -> ApiError:
    return ApiError(
        f"Plugin installation not found: {plugin_install_id}",
        HttpErrorCode.NOT_FOUND,
        {"domainCode": "not_found", "domainTags": ["not-found"]},
    )


class ViewPluginService:
    async def _table_base_id(self, table_id: str) -> str:
        table = await field_repository.get_table_meta_by_id(table_id)
        if table is None:
            raise ApiError(
                f"Table not found: {table_id}",
                HttpErrorCode.NOT_FOUND,
                {"localization": {"i18nKey": "httpErrors.table.notFound"}},
            )
        return table["base_id"]

    async def _load_plugin(self, plugin_id: str, user_id: str) -> dict[str, Any]:
        async with db_engine.session() as session:
            row = (
                await session.execute(
                    select(
                        Plugin.id,
                        Plugin.name,
                        Plugin.logo,
                        Plugin.positions,
                        Plugin.url,
                    ).where(
                        Plugin.id == plugin_id,
                        (Plugin.status == "published")
                        | ((Plugin.status != "published") & (Plugin.created_by == user_id)),
                    )
                )
            ).first()
        if row is None:
            raise _plugin_not_found(plugin_id)
        return {
            "id": row[0],
            "name": row[1],
            "logo": row[2],
            "positions": json.loads(row[3] or "[]"),
            "url": row[4],
        }

    async def _view_exists(self, table_id: str, view_id: str) -> None:
        async with db_engine.session() as session:
            row = (
                await session.execute(
                    text(
                        'SELECT 1 FROM "view" WHERE id = :v AND table_id = :t '
                        "AND deleted_time IS NULL"
                    ),
                    {"v": view_id, "t": table_id},
                )
            ).first()
        if row is None:
            raise ApiError(
                f"View not found with id: {view_id} and tableId: {table_id}",
                HttpErrorCode.NOT_FOUND,
                {"localization": {"i18nKey": "httpErrors.view.notFound"}},
            )

    async def install(self, table_id: str, ro: dict[str, Any]) -> dict[str, Any]:
        from ...core import cls

        user_id = cls.get("user.id")
        plugin_id = ro.get("pluginId")
        plugin = await self._load_plugin(plugin_id, user_id)
        if _POSITION_VIEW not in plugin["positions"]:
            raise ApiError(
                f"Plugin {plugin_id} does not support install in view",
                HttpErrorCode.VALIDATION_ERROR,
                {"domainCode": "validation.invalid", "domainTags": ["validation"]},
            )
        base_id = await self._table_base_id(table_id)
        view_name = ro.get("name") or plugin["name"]
        plugin_install_id = new_id(IdPrefix.PLUGIN_INSTALL)
        view = await ViewService().create_view(
            table_id,
            ViewCreateBody.zod_validate(
                {
                    "name": view_name,
                    "type": "plugin",
                    "options": {
                        "pluginInstallId": plugin_install_id,
                        "pluginId": plugin_id,
                        "pluginLogo": plugin["logo"],
                    },
                }
            ),
        )
        async with db_engine.session() as session:
            await session.execute(
                PluginInstall.__table__.insert().values(
                    id=plugin_install_id,
                    plugin_id=plugin_id,
                    base_id=base_id,
                    name=view_name,
                    position_id=view["id"],
                    position=_POSITION_VIEW,
                    created_by=user_id,
                )
            )
            await session.commit()
        return {
            "pluginId": plugin_id,
            "pluginInstallId": plugin_install_id,
            "name": view_name,
            "viewId": view["id"],
        }

    async def get(self, table_id: str, view_id: str) -> dict[str, Any]:
        await self._view_exists(table_id, view_id)
        base_id = await self._table_base_id(table_id)
        async with db_engine.session() as session:
            row = (
                await session.execute(
                    select(
                        PluginInstall.id,
                        PluginInstall.plugin_id,
                        PluginInstall.name,
                        PluginInstall.storage,
                        Plugin.url,
                    )
                    .join(Plugin, Plugin.id == PluginInstall.plugin_id, isouter=True)
                    .where(
                        PluginInstall.position_id == view_id,
                        PluginInstall.position == _POSITION_VIEW,
                    )
                )
            ).first()
        if row is None:
            raise _install_not_found_for_view(view_id)
        vo: dict[str, Any] = {
            "pluginId": row[1],
            "pluginInstallId": row[0],
            "baseId": base_id,
            "name": row[2],
        }
        if row[4]:
            vo["url"] = row[4]
        if row[3]:
            vo["storage"] = json.loads(row[3])
        return vo

    async def update_storage(
        self, table_id: str, view_id: str, plugin_install_id: str, storage: Any
    ) -> dict[str, Any]:
        await self._view_exists(table_id, view_id)
        async with db_engine.session() as session:
            row = (
                await session.execute(
                    select(PluginInstall.id).where(
                        PluginInstall.id == plugin_install_id,
                        PluginInstall.position_id == view_id,
                        PluginInstall.position == _POSITION_VIEW,
                    )
                )
            ).first()
            if row is None:
                raise _install_not_found(plugin_install_id)
            await session.execute(
                update(PluginInstall)
                .where(PluginInstall.id == plugin_install_id)
                .values(storage=json.dumps(storage) if storage is not None else None)
            )
            await session.commit()
        vo: dict[str, Any] = {
            "tableId": table_id,
            "viewId": view_id,
            "pluginInstallId": plugin_install_id,
        }
        if storage is not None:
            vo["storage"] = storage
        return vo
