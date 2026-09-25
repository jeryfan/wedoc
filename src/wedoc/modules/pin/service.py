"""Pin service — ports features/pin/pin.service.ts."""

import json
from datetime import UTC, datetime
from typing import Any

from ...core import cls
from ...core.errors import ApiError, HttpErrorCode
from ...core.storage import get_public_full_storage_url
from . import repository
from .schemas import AddPinRo, DeletePinRo, UpdatePinOrderRo

_EPSILON2 = 2 * 2.220446049250313e-16
_FOLDER = "folder"
_TABLE = "table"


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _pin_vo(row: dict[str, Any]) -> dict[str, Any]:
    return {**row, "createdTime": _iso(row["createdTime"])}


class PinService:
    async def add_pin(self, ro: AddPinRo) -> dict[str, Any]:
        user_id = cls.get("user.id")
        max_order = await repository.get_max_order(user_id)
        row = await repository.add_pin(user_id, ro.type, ro.id, max_order + 1)
        return _pin_vo(row)

    async def delete_pin(self, ro: DeletePinRo) -> dict[str, Any]:
        user_id = cls.get("user.id")
        row = await repository.delete_pin(user_id, ro.type, ro.id)
        return _pin_vo(row)

    async def get_list(self) -> list[dict[str, Any]]:
        user_id = cls.get("user.id")
        pins = await repository.list_pins(user_id)

        ids_by_type: dict[str, list[str]] = {}
        for pin in pins:
            ids_by_type.setdefault(pin["type"], []).append(pin["resourceId"])

        base_list = await repository.fetch_bases(ids_by_type.get("base", []))
        space_list = await repository.fetch_spaces(ids_by_type.get("space", []))
        table_list = await repository.fetch_tables(ids_by_type.get("table", []))
        view_list = await repository.fetch_views(ids_by_type.get("view", []))
        dashboard_list = await repository.fetch_dashboards(ids_by_type.get("dashboard", []))

        resource_maps = {
            "base": {r["id"]: r for r in base_list},
            "space": {r["id"]: r for r in space_list},
            "table": {r["id"]: r for r in table_list},
            "view": {r["id"]: r for r in view_list},
            "dashboard": {r["id"]: r for r in dashboard_list},
            "workflow": {},
            "app": {},
        }

        result: list[dict[str, Any]] = []
        for pin in pins:
            resource = self._transform_resource(pin["type"], pin["resourceId"], resource_maps)
            if resource is None:
                continue
            entry: dict[str, Any] = {
                "id": pin["resourceId"],
                "type": pin["type"],
                "order": pin["order"],
            }
            for key, value in resource.items():
                if value is not None:
                    entry[key] = value
            result.append(entry)
        return result

    def _transform_resource(
        self, pin_type: str, resource_id: str, resource_maps: dict[str, Any]
    ) -> dict[str, Any] | None:
        resource = resource_maps.get(pin_type, {}).get(resource_id)
        if not resource:
            return None
        if pin_type == "base":
            return {"name": resource["name"], "icon": resource.get("icon")}
        if pin_type in ("space", "dashboard", "workflow", "app"):
            return {"name": resource["name"], "parentBaseId": resource.get("baseId")}
        if pin_type == "table":
            return {
                "name": resource["name"],
                "parentBaseId": resource.get("baseId"),
                "icon": resource.get("icon"),
            }
        if pin_type == "view":
            options = resource.get("options")
            plugin_logo = None
            if options:
                try:
                    plugin_logo = json.loads(options).get("pluginLogo")
                except (json.JSONDecodeError, AttributeError):
                    plugin_logo = None
            return {
                "name": resource["name"],
                "parentBaseId": resource.get("baseId"),
                "viewMeta": {
                    "tableId": resource.get("tableId"),
                    "type": resource.get("type"),
                    "pluginLogo": get_public_full_storage_url(plugin_logo)
                    if plugin_logo
                    else None,
                },
            }
        return None

    async def update_order(self, data: UpdatePinOrderRo) -> None:
        user_id = cls.get("user.id")
        item = await repository.find_pin(user_id, data.type, data.id)
        if item is None:
            raise ApiError(
                "Pin not found",
                HttpErrorCode.NOT_FOUND,
                {"localization": {"i18nKey": "httpErrors.pin.notFound"}},
            )
        anchor = await repository.find_pin(user_id, data.anchorType, data.anchorId)
        if anchor is None:
            raise ApiError(
                "Pin Anchor not found",
                HttpErrorCode.NOT_FOUND,
                {"localization": {"i18nKey": "httpErrors.pin.anchorNotFound"}},
            )
        await self._reorder(user_id, data.type, data.position, item, anchor)

    async def _reorder(
        self,
        user_id: str,
        pin_type: str,
        position: str,
        item: dict[str, Any],
        anchor: dict[str, Any],
    ) -> None:
        before = position == "before"
        op = "lt" if before else "gt"
        align = "desc" if before else "asc"
        next_item = await repository.get_next_pin(pin_type, anchor["order"], op, align)
        order = (
            (next_item["order"] + anchor["order"]) / 2
            if next_item
            else anchor["order"] + (-1 if before else 1)
        )
        if abs(order - anchor["order"]) < _EPSILON2:
            await repository.shuffle_pins(
                user_id, anchor["order"], op, -1 if before else 1
            )
            await self._reorder(user_id, pin_type, position, item, anchor)
            return
        await repository.update_pin_order(item["id"], order)

    async def get_entry_map(self) -> dict[str, str]:
        user_id = cls.get("user.id")
        pins = await repository.find_pins_for_entry(user_id)
        base_ids = [p["resourceId"] for p in pins if p["type"] == "base"]
        table_ids = [p["resourceId"] for p in pins if p["type"] == _TABLE]
        tables = await repository.tables_by_ids(table_ids)
        base_entry_map = await self._get_base_entry_map(user_id, base_ids)
        table_entry_map = await self._get_table_entry_urls(
            user_id, [{"tableId": t["id"], "baseId": t["baseId"]} for t in tables]
        )
        return {**base_entry_map, **table_entry_map}

    async def _get_base_entry_map(self, user_id: str, base_ids: list[str]) -> dict[str, str]:
        if not base_ids:
            return {}
        node_visits = await repository.node_visits(user_id, base_ids)
        latest_node_by_base: dict[str, dict[str, Any]] = {}
        for visit in node_visits:
            latest_node_by_base.setdefault(visit["parentResourceId"], visit)
        table_id_to_base: dict[str, str] = {}
        for visited_base_id, node in latest_node_by_base.items():
            if node["resourceType"] == _TABLE:
                table_id_to_base[node["resourceId"]] = visited_base_id
        never_visited = [bid for bid in base_ids if bid not in latest_node_by_base]
        await self._collect_default_table_entries(never_visited, table_id_to_base)
        if not table_id_to_base:
            return {}
        url_by_table = await self._resolve_table_entry_urls(user_id, table_id_to_base)
        entry_map: dict[str, str] = {}
        for table_id, entry_base_id in table_id_to_base.items():
            url = url_by_table.get(table_id)
            if url:
                entry_map[entry_base_id] = url
        return entry_map

    async def _get_table_entry_urls(
        self, user_id: str, tables: list[dict[str, str]]
    ) -> dict[str, str]:
        if not tables:
            return {}
        return await self._resolve_table_entry_urls(
            user_id, {t["tableId"]: t["baseId"] for t in tables}
        )

    async def _collect_default_table_entries(
        self, base_ids: list[str], table_id_to_base: dict[str, str]
    ) -> None:
        if not base_ids:
            return
        nodes = await repository.first_nodes_by_base(base_ids)
        first_node_by_base: dict[str, dict[str, Any]] = {}
        for node in nodes:
            if node["resourceType"] == _FOLDER:
                continue
            first_node_by_base.setdefault(node["baseId"], node)
        for default_base_id, node in first_node_by_base.items():
            if node["resourceType"] == _TABLE:
                table_id_to_base[node["resourceId"]] = default_base_id

    async def _resolve_table_entry_urls(
        self, user_id: str, table_id_to_base: dict[str, str]
    ) -> dict[str, str]:
        entry_map: dict[str, str] = {}
        table_ids = list(table_id_to_base.keys())
        tables = await repository.tables_by_ids(table_ids)
        view_visits = await repository.view_visits(user_id, table_ids)
        views = await repository.views_by_tables(table_ids)
        latest_view_by_table: dict[str, str] = {}
        for visit in view_visits:
            latest_view_by_table.setdefault(visit["parentResourceId"], visit["resourceId"])
        view_ids_by_table: dict[str, list[str]] = {}
        for view in views:
            view_ids_by_table.setdefault(view["tableId"], []).append(view["id"])
        for table in tables:
            entry_base_id = table_id_to_base.get(table["id"])
            table_view_ids = view_ids_by_table.get(table["id"])
            if entry_base_id != table["baseId"] or not entry_base_id or not table_view_ids:
                continue
            last_view_id = latest_view_by_table.get(table["id"])
            view_id = last_view_id if last_view_id and last_view_id in table_view_ids else None
            entry_map[table["id"]] = (
                f"/base/{entry_base_id}/table/{table['id']}/{view_id}"
                if view_id
                else f"/base/{entry_base_id}/table/{table['id']}"
            )
        return entry_map
