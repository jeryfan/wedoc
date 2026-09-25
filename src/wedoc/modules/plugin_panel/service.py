"""Plugin-panel service — ports plugin-panel.service.ts."""

import json
from typing import Any

from ...core import cls
from ...core.errors import ApiError, HttpErrorCode
from ...core.ids import IdPrefix, cuid, random_string
from ...db import engine as db_engine
from ...db.models_meta import Collaborator
from ..space.service import get_uniq_name
from . import repository
from .schemas import (
    CreatePanelRo,
    DuplicateInstalledRo,
    DuplicatePanelRo,
    InstallRo,
    RenamePanelRo,
    UpdateStorageRo,
)

_MAX_SAFE_INTEGER = 9007199254740991
_POS = "panel"


def _panel_id() -> str:
    return str(IdPrefix.PLUGIN_PANEL) + random_string(16)


def _install_id() -> str:
    return str(IdPrefix.PLUGIN_INSTALL) + random_string(16)


class PluginPanelService:
    async def create_panel(self, table_id: str, ro: CreatePanelRo) -> dict[str, Any]:
        return await repository.create_panel(
            _panel_id(), ro.name, table_id, cls.get("user.id")
        )

    async def get_panels(self, table_id: str) -> list[dict[str, Any]]:
        return await repository.list_panels(table_id)

    async def get_panel(self, table_id: str, panel_id: str) -> dict[str, Any]:
        panel = await repository.get_panel(table_id, panel_id)
        if panel is None:
            raise repository.panel_not_found()
        installs = await repository.panel_installs(panel_id)
        plugin_map = {}
        for p in installs:
            item = {
                "id": p["pluginId"],
                "name": p["name"],
                "positionId": p["positionId"],
                "pluginInstallId": p["id"],
            }
            if p["url"] is not None:
                item["url"] = p["url"]
            plugin_map[p["id"]] = item
        out: dict[str, Any] = {"id": panel["id"], "name": panel["name"]}
        if panel["layout"]:
            out["layout"] = json.loads(panel["layout"])
        out["pluginMap"] = plugin_map
        return out

    async def rename_panel(self, table_id: str, panel_id: str, ro: RenamePanelRo) -> dict[str, Any]:
        return await repository.rename_panel(table_id, panel_id, ro.name, cls.get("user.id"))

    async def delete_panel(self, table_id: str, panel_id: str) -> None:
        await repository.delete_panel(table_id, panel_id)

    async def update_layout(
        self, table_id: str, panel_id: str, layout: list[dict[str, Any]]
    ) -> dict[str, Any]:
        row = await repository.update_layout(
            table_id, panel_id, json.dumps(layout), cls.get("user.id")
        )
        out: dict[str, Any] = {"id": row["id"]}
        if row["layout"]:
            out["layout"] = json.loads(row["layout"])
        return out

    async def install(self, table_id: str, panel_id: str, ro: InstallRo) -> dict[str, Any]:
        user_id = cls.get("user.id")
        base_id = await repository.get_base_id(table_id)
        found, plugin_user = await repository.get_plugin_user(ro.pluginId)
        if not found:
            raise ApiError(
                "Plugin not found",
                HttpErrorCode.NOT_FOUND,
                {"localization": {"i18nKey": "httpErrors.plugin.notFound"}},
            )
        install_id = _install_id()
        name = ro.name or await self._plugin_name(ro.pluginId)
        await repository.create_install(
            {
                "id": install_id,
                "plugin_id": ro.pluginId,
                "base_id": base_id,
                "name": name,
                "position": _POS,
                "position_id": panel_id,
                "created_by": user_id,
            }
        )
        if plugin_user:
            exist = await self._collaborator_count(plugin_user, base_id)
            if not exist:
                await self._invite(base_id, plugin_user, user_id)
        ok, layout_json = await repository.get_layout(table_id, panel_id)
        if not ok:
            raise repository.panel_not_found()
        layout = json.loads(layout_json) if layout_json else []
        layout.append(
            {"pluginInstallId": install_id, "x": 0, "y": _MAX_SAFE_INTEGER, "w": 1, "h": 3}
        )
        await repository.set_layout(panel_id, json.dumps(layout))
        return {"pluginId": ro.pluginId, "name": name, "pluginInstallId": install_id}

    async def _plugin_name(self, plugin_id: str) -> str:
        from sqlalchemy import select

        from ...db.models_meta import Plugin

        async with db_engine.session() as session:
            row = (
                await session.execute(select(Plugin.name).where(Plugin.id == plugin_id))
            ).first()
        return row[0] if row else ""

    async def _collaborator_count(self, principal_id: str, base_id: str) -> int:
        from sqlalchemy import func, select

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

    async def _invite(self, base_id: str, plugin_user: str, user_id: str) -> None:
        async with db_engine.session() as session:
            await session.execute(
                Collaborator.__table__.insert().values(
                    id=cuid(),
                    role_name="owner",
                    resource_type="base",
                    resource_id=base_id,
                    principal_id=plugin_user,
                    principal_type="user",
                    created_by=user_id,
                )
            )
            await session.commit()

    async def remove_plugin(self, table_id: str, panel_id: str, plugin_install_id: str) -> None:
        base_id = await repository.get_base_id(table_id)
        await repository.delete_install(base_id, panel_id, plugin_install_id)
        ok, layout_json = await repository.get_layout(table_id, panel_id)
        if not ok:
            raise repository.panel_not_found()
        layout = json.loads(layout_json) if layout_json else []
        new_layout = [i for i in layout if i.get("pluginInstallId") != plugin_install_id]
        if len(new_layout) != len(layout):
            await repository.set_layout(panel_id, json.dumps(new_layout))

    async def rename_plugin(
        self, table_id: str, panel_id: str, plugin_install_id: str, ro: RenamePanelRo
    ) -> dict[str, Any]:
        base_id = await repository.get_base_id(table_id)
        await repository.rename_install(
            base_id, panel_id, plugin_install_id, ro.name, cls.get("user.id")
        )
        return {"id": plugin_install_id, "name": ro.name}

    async def update_plugin_storage(
        self, table_id: str, panel_id: str, plugin_install_id: str, ro: UpdateStorageRo
    ) -> dict[str, Any]:
        base_id = await repository.get_base_id(table_id)
        storage_json = json.dumps(ro.storage) if ro.storage else None
        stored = await repository.update_install_storage(
            base_id, panel_id, plugin_install_id, storage_json, cls.get("user.id")
        )
        out: dict[str, Any] = {
            "pluginInstallId": plugin_install_id,
            "tableId": table_id,
            "pluginPanelId": panel_id,
        }
        if stored:
            out["storage"] = json.loads(stored)
        return out

    async def get_plugin(
        self, table_id: str, panel_id: str, plugin_install_id: str
    ) -> dict[str, Any]:
        base_id = await repository.get_base_id(table_id)
        install = await repository.get_install(base_id, panel_id, plugin_install_id)
        out: dict[str, Any] = {
            "baseId": base_id,
            "name": install["name"],
            "tableId": table_id,
            "pluginId": install["pluginId"],
            "pluginInstallId": install["id"],
        }
        if install["storage"]:
            out["storage"] = json.loads(install["storage"])
        return out

    async def duplicate_panel(
        self, table_id: str, panel_id: str, ro: DuplicatePanelRo
    ) -> dict[str, Any]:
        panel = await repository.get_panel(table_id, panel_id)
        if panel is None:
            raise repository.panel_not_found()
        base_id = await repository.get_base_id(table_id)
        names = await repository.panel_names(table_id)
        new_name = get_uniq_name(ro.name or panel["name"], names)
        new_panel_id = _panel_id()
        user_id = cls.get("user.id")
        await repository.create_panel(new_panel_id, new_name, table_id, user_id)
        installs = await repository.installs_for_duplicate(panel_id)
        id_map: dict[str, str] = {}
        for inst in installs:
            new_pi = _install_id()
            id_map[inst["id"]] = new_pi
            await repository.clone_install(
                base_id, new_panel_id, inst["pluginId"], inst["name"], inst["storage"], new_pi,
                user_id,
            )
        if panel["layout"]:
            layout = json.loads(panel["layout"])
            new_layout = [
                {**i, "pluginInstallId": id_map.get(i["pluginInstallId"], i["pluginInstallId"])}
                for i in layout
            ]
            await repository.set_layout(new_panel_id, json.dumps(new_layout))
        return {"id": new_panel_id, "name": new_name}

    async def duplicate_plugin(
        self, table_id: str, panel_id: str, plugin_install_id: str, ro: DuplicateInstalledRo
    ) -> dict[str, Any]:
        base_id = await repository.get_base_id(table_id)
        source = await repository.find_install_for_duplicate(base_id, panel_id, plugin_install_id)
        if source is None:
            raise ApiError(
                "Plugin install not found",
                HttpErrorCode.NOT_FOUND,
                {"localization": {"i18nKey": "httpErrors.plugin.notFound"}},
            )
        names = await repository.install_names(base_id, panel_id)
        new_name = get_uniq_name(ro.name or source["name"], names)
        new_pi = _install_id()
        await repository.clone_install(
            base_id, panel_id, source["pluginId"], new_name, source["storage"], new_pi,
            cls.get("user.id"),
        )
        ok, layout_json = await repository.get_layout(table_id, panel_id)
        layout = json.loads(layout_json) if ok and layout_json else []
        src = next((i for i in layout if i.get("pluginInstallId") == plugin_install_id), None)
        layout.append(
            {
                "pluginInstallId": new_pi,
                "x": (len(layout) * 2) % 12,
                "y": _MAX_SAFE_INTEGER,
                "w": (src or {}).get("w", 2),
                "h": (src or {}).get("h", 2),
            }
        )
        await repository.set_layout(panel_id, json.dumps(layout))
        return {"id": new_pi, "name": new_name}
