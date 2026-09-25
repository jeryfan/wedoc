"""Dashboard service — ports features/dashboard/dashboard.service.ts."""

import json
from typing import Any

from ...core import cls
from ...core.ids import IdPrefix, cuid, random_string
from ...db import engine as db_engine
from ...db.models_meta import Collaborator
from ..space.service import get_uniq_name
from . import repository
from .schemas import (
    CreateDashboardRo,
    DuplicateDashboardRo,
    DuplicateInstalledPluginRo,
    InstallPluginRo,
    UpdateStorageRo,
)

_MAX_SAFE_INTEGER = 9007199254740991
_PLUGIN_POSITION_DASHBOARD = "dashboard"


def _dashboard_id() -> str:
    return str(IdPrefix.DASHBOARD) + random_string(12)


def _plugin_install_id() -> str:
    return str(IdPrefix.PLUGIN_INSTALL) + random_string(16)


class DashboardService:
    async def get_dashboard(self, base_id: str) -> list[dict[str, Any]]:
        return await repository.list_dashboards(base_id)

    async def get_dashboard_by_id(self, base_id: str, dashboard_id: str) -> dict[str, Any]:
        dashboard = await repository.get_dashboard(base_id, dashboard_id)
        installs = await repository.list_installs_with_url(base_id, dashboard_id)
        plugin_map = {
            p["id"]: {
                "id": p["pluginId"],
                "pluginInstallId": p["id"],
                "name": p["name"],
                **({"url": p["url"]} if p["url"] is not None else {}),
            }
            for p in installs
        }
        out: dict[str, Any] = {"id": dashboard["id"], "name": dashboard["name"]}
        if dashboard["layout"]:
            out["layout"] = json.loads(dashboard["layout"])
        out["pluginMap"] = plugin_map
        return out

    async def create_dashboard(self, base_id: str, ro: CreateDashboardRo) -> dict[str, Any]:
        user_id = cls.get("user.id")
        dashboard_id = _dashboard_id()
        await repository.create_dashboard(dashboard_id, base_id, ro.name, user_id)
        return {"id": dashboard_id, "name": ro.name}

    async def rename_dashboard(self, base_id: str, dashboard_id: str, name: str) -> dict[str, Any]:
        return await repository.rename_dashboard(base_id, dashboard_id, name)

    async def update_layout(
        self, base_id: str, dashboard_id: str, layout: list[dict[str, Any]]
    ) -> dict[str, Any]:
        row = await repository.update_layout(base_id, dashboard_id, json.dumps(layout))
        out: dict[str, Any] = {"id": row["id"], "name": row["name"]}
        if row["layout"]:
            out["layout"] = json.loads(row["layout"])
        return out

    async def delete_dashboard(self, base_id: str, dashboard_id: str) -> None:
        await repository.delete_dashboard(base_id, dashboard_id)

    async def install_plugin(
        self, base_id: str, dashboard_id: str, ro: InstallPluginRo
    ) -> dict[str, Any]:
        user_id = cls.get("user.id")
        plugin = await repository.validate_plugin_published(ro.pluginId, user_id)
        install_id = _plugin_install_id()
        created = await repository.install_plugin_row(
            {
                "id": install_id,
                "base_id": base_id,
                "position_id": dashboard_id,
                "position": _PLUGIN_POSITION_DASHBOARD,
                "name": ro.name,
                "plugin_id": ro.pluginId,
                "created_by": user_id,
            }
        )
        if plugin["pluginUser"]:
            exist = await repository.base_collaborator_count(plugin["pluginUser"], base_id)
            if not exist:
                await self._invite_plugin_user(base_id, plugin["pluginUser"], user_id)
        layout_json = await repository.get_layout(base_id, dashboard_id)
        layout = json.loads(layout_json) if layout_json else []
        layout.append(
            {
                "pluginInstallId": install_id,
                "x": (len(layout) * 2) % 12,
                "y": _MAX_SAFE_INTEGER,
                "w": 2,
                "h": 2,
            }
        )
        await repository.set_layout(dashboard_id, json.dumps(layout))
        return {
            "id": dashboard_id,
            "pluginId": created["pluginId"],
            "pluginInstallId": install_id,
            "name": ro.name,
        }

    async def _invite_plugin_user(self, base_id: str, plugin_user: str, user_id: str) -> None:
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

    async def remove_plugin(self, base_id: str, dashboard_id: str, plugin_install_id: str) -> None:
        user_id = cls.get("user.id")
        await repository.delete_install(base_id, dashboard_id, plugin_install_id, user_id)
        layout_json = await repository.get_layout(base_id, dashboard_id)
        layout = json.loads(layout_json) if layout_json else []
        new_layout = [i for i in layout if i.get("pluginInstallId") != plugin_install_id]
        if len(new_layout) != len(layout):
            await repository.set_layout(dashboard_id, json.dumps(new_layout))

    async def rename_plugin(
        self, base_id: str, dashboard_id: str, plugin_install_id: str, name: str
    ) -> dict[str, Any]:
        user_id = cls.get("user.id")
        await repository.dashboard_exists(base_id, dashboard_id)
        plugin = await repository.get_install_or_plugin_404(
            base_id, dashboard_id, plugin_install_id, user_id
        )
        await repository.rename_install(plugin_install_id, name)
        return {"id": plugin["pluginId"], "pluginInstallId": plugin_install_id, "name": name}

    async def update_plugin_storage(
        self, base_id: str, dashboard_id: str, plugin_install_id: str, ro: UpdateStorageRo
    ) -> dict[str, Any]:
        user_id = cls.get("user.id")
        await repository.dashboard_exists(base_id, dashboard_id)
        await repository.get_install_or_plugin_404(
            base_id, dashboard_id, plugin_install_id, user_id
        )
        storage_json = json.dumps(ro.storage) if ro.storage else None
        stored = await repository.update_install_storage(plugin_install_id, storage_json)
        out: dict[str, Any] = {
            "baseId": base_id,
            "dashboardId": dashboard_id,
            "pluginInstallId": plugin_install_id,
        }
        if stored:
            out["storage"] = json.loads(stored)
        return out

    async def get_plugin_install(
        self, base_id: str, dashboard_id: str, plugin_install_id: str
    ) -> dict[str, Any]:
        user_id = cls.get("user.id")
        await repository.dashboard_exists(base_id, dashboard_id)
        plugin = await repository.get_install_or_plugin_404(
            base_id, dashboard_id, plugin_install_id, user_id
        )
        out: dict[str, Any] = {
            "name": plugin["name"],
            "baseId": plugin["baseId"],
            "pluginId": plugin["pluginId"],
            "pluginInstallId": plugin["id"],
        }
        if plugin["storage"]:
            out["storage"] = json.loads(plugin["storage"])
        return out

    async def duplicate_dashboard(
        self, base_id: str, dashboard_id: str, ro: DuplicateDashboardRo
    ) -> dict[str, Any]:
        source = await repository.get_dashboard(base_id, dashboard_id)
        names = await repository.dashboard_names(base_id)
        new_name = get_uniq_name(ro.name or source["name"], names)
        new_dashboard_id = _dashboard_id()
        user_id = cls.get("user.id")
        await repository.create_dashboard(new_dashboard_id, base_id, new_name, user_id)
        # copy installed plugins + remap layout ids
        installs = await repository.list_installs_for_duplicate(base_id, dashboard_id)
        id_map: dict[str, str] = {}
        for inst in installs:
            new_pi = _plugin_install_id()
            id_map[inst["id"]] = new_pi
            await repository.clone_install(
                {
                    "pluginId": inst["pluginId"],
                    "baseId": base_id,
                    "positionId": new_dashboard_id,
                    "position": _PLUGIN_POSITION_DASHBOARD,
                    "storage": inst["storage"],
                },
                new_pi,
                inst["name"],
                user_id,
            )
        if source["layout"]:
            layout = json.loads(source["layout"])
            new_layout = [
                {
                    **item,
                    "pluginInstallId": id_map.get(
                        item["pluginInstallId"], item["pluginInstallId"]
                    ),
                }
                for item in layout
            ]
            await repository.set_layout(new_dashboard_id, json.dumps(new_layout))
        return {"id": new_dashboard_id, "name": new_name}

    async def duplicate_dashboard_installed_plugin(
        self,
        base_id: str,
        dashboard_id: str,
        plugin_install_id: str,
        ro: DuplicateInstalledPluginRo,
    ) -> dict[str, Any]:
        user_id = cls.get("user.id")
        await repository.dashboard_exists(base_id, dashboard_id)
        source = await repository.get_install_or_plugin_404(
            base_id, dashboard_id, plugin_install_id, user_id
        )
        names = await repository.install_names(base_id, dashboard_id)
        new_name = get_uniq_name(ro.name or source["name"], names)
        new_pi = _plugin_install_id()
        await repository.clone_install(source, new_pi, new_name, user_id)
        layout_json = await repository.get_layout(base_id, dashboard_id)
        layout = json.loads(layout_json) if layout_json else []
        src_layout = next(
            (i for i in layout if i.get("pluginInstallId") == plugin_install_id), None
        )
        layout.append(
            {
                "pluginInstallId": new_pi,
                "x": (len(layout) * 2) % 12,
                "y": _MAX_SAFE_INTEGER,
                "w": (src_layout or {}).get("w", 2),
                "h": (src_layout or {}).get("h", 2),
            }
        )
        await repository.set_layout(dashboard_id, json.dumps(layout))
        return {"id": new_pi, "name": new_name}
