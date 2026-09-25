"""Plugin-context-menu service — ports plugin-context-menu.service.ts."""

import json
from typing import Any

from sqlalchemy import func, select

from ...core import cls
from ...core.errors import ApiError, HttpErrorCode
from ...core.ids import IdPrefix, cuid, random_string
from ...core.storage import get_public_full_storage_url
from ...db import engine as db_engine
from ...db.models_meta import Collaborator
from . import repository
from .schemas import InstallRo, MoveRo, RenameRo, UpdateStorageRo

_EPSILON2 = 2 * 2.220446049250313e-16
_POS = "contextMenu"


def _install_id() -> str:
    return str(IdPrefix.PLUGIN_INSTALL) + random_string(16)


class PluginContextMenuService:
    async def install(self, table_id: str, ro: InstallRo) -> dict[str, Any]:
        found, name, plugin_user = await repository.plugin_name_and_user(ro.pluginId)
        if not found:
            raise ApiError(
                "Plugin not found",
                HttpErrorCode.NOT_FOUND,
                {"localization": {"i18nKey": "httpErrors.plugin.notFound"}},
            )
        base_id = await repository.get_base_id(table_id)
        plugin_name = ro.name or name
        user_id = cls.get("user.id")
        install_id = _install_id()
        await repository.create_install(
            {
                "id": install_id,
                "plugin_id": ro.pluginId,
                "base_id": base_id,
                "name": plugin_name,
                "position_id": table_id,
                "position": _POS,
                "created_by": user_id,
            }
        )
        if plugin_user:
            exist = await self._collaborator_count(plugin_user, base_id)
            if not exist:
                await self._invite(base_id, plugin_user, user_id)
        order = await repository.max_order(table_id)
        await repository.create_menu(cuid(), table_id, install_id, order + 1, user_id)
        return {"pluginInstallId": install_id, "name": plugin_name, "order": order + 1}

    async def _collaborator_count(self, principal_id: str, base_id: str) -> int:
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

    async def get_list(self, table_id: str) -> list[dict[str, Any]]:
        await repository.get_base_id(table_id)
        rows = await repository.list_menu(table_id)
        return [
            {
                "pluginInstallId": r["pluginInstallId"],
                "name": r["name"],
                "pluginId": r["pluginId"],
                "logo": get_public_full_storage_url(r["logo"]),
                "order": r["order"],
            }
            for r in rows
        ]

    async def get(self, table_id: str, plugin_install_id: str) -> dict[str, Any]:
        base_id = await repository.get_base_id(table_id)
        res = await repository.get_install(base_id, table_id, plugin_install_id)
        if res is None:
            raise repository.install_not_found()
        out: dict[str, Any] = {
            "tableId": table_id,
            "positionId": res["positionId"],
            "pluginId": res["pluginId"],
            "pluginInstallId": res["id"],
            "name": res["name"],
        }
        if res["url"] is not None:
            out["url"] = res["url"]
        if res["config"]:
            out["config"] = json.loads(res["config"])
        return out

    async def get_storage(self, table_id: str, plugin_install_id: str) -> dict[str, Any]:
        base_id = await repository.get_base_id(table_id)
        res = await repository.get_install(base_id, table_id, plugin_install_id)
        if res is None:
            raise repository.install_not_found()
        out: dict[str, Any] = {
            "name": res["name"],
            "tableId": table_id,
            "pluginId": res["pluginId"],
            "pluginInstallId": res["id"],
        }
        if res["storage"]:
            out["storage"] = json.loads(res["storage"])
        return out

    async def rename(self, table_id: str, plugin_install_id: str, ro: RenameRo) -> dict[str, Any]:
        base_id = await repository.get_base_id(table_id)
        await repository.rename_install(base_id, table_id, plugin_install_id, ro.name)
        return {"pluginInstallId": plugin_install_id, "name": ro.name}

    async def update_storage(
        self, table_id: str, plugin_install_id: str, ro: UpdateStorageRo
    ) -> dict[str, Any]:
        base_id = await repository.get_base_id(table_id)
        storage_json = json.dumps(ro.storage) if ro.storage is not None else None
        stored = await repository.update_storage(
            base_id, table_id, plugin_install_id, storage_json
        )
        out: dict[str, Any] = {"tableId": table_id, "pluginInstallId": plugin_install_id}
        if stored:
            out["storage"] = json.loads(stored)
        return out

    async def delete(self, table_id: str, plugin_install_id: str) -> None:
        base_id = await repository.get_base_id(table_id)
        await repository.delete_menu_and_install(base_id, table_id, plugin_install_id)

    async def move(self, table_id: str, plugin_install_id: str, ro: MoveRo) -> None:
        item = await repository.find_menu(table_id, plugin_install_id)
        if item is None:
            raise ApiError(
                "Plugin Context Menu not found",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.pluginContextMenu.notFound"}},
            )
        anchor = await repository.find_menu(table_id, ro.anchorId)
        if anchor is None:
            raise ApiError(
                "Plugin Context Menu Anchor not found",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.pluginContextMenu.anchorNotFound"}},
            )
        await self._reorder(table_id, ro.position, item, anchor)

    async def _reorder(
        self, table_id: str, position: str, item: dict[str, Any], anchor: dict[str, Any]
    ) -> None:
        before = position == "before"
        op = "lt" if before else "gt"
        align = "desc" if before else "asc"
        nxt = await repository.get_next_menu(table_id, anchor["order"], op, align)
        if nxt:
            order = (nxt["order"] + anchor["order"]) / 2
        else:
            order = anchor["order"] + (-1 if before else 1)
        if abs(order - anchor["order"]) < _EPSILON2:
            await repository.shuffle_menu(table_id, anchor["order"], "gte" if before else "gt")
            await self._reorder(table_id, position, item, anchor)
            return
        await repository.update_menu_order(table_id, item["id"], order)
