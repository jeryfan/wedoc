"""view domain service — ports features/view (view-open-api.service.ts v2 flows).

Only the REST-visible surface is ported: view meta CRUD + property updates +
share lifecycle + duplicate. filter-link-records waits for link fields; the
socket snapshot/doc-ids endpoints are M3 realtime.
"""

import json
from datetime import UTC, datetime
from typing import Any

from ...core import cls
from ...core.errors import ApiError, HttpErrorCode
from ...core.ids import IdPrefix, new_id
from ..field.repository import get_table_meta_by_id
from ..record import repository as record_repository
from ..table import repository as table_repository
from . import repository
from .schemas import (
    ColumnMetaItem,
    ShareMetaBody,
    ViewCreateBody,
    ViewNameBody,
)

JSON_KEYS = ("options", "sort", "filter", "group", "shareMeta")


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _dump(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, separators=(",", ":"))


def _collect_filter_link_records(
    link_fields: dict[str, str], filter_obj: dict[str, Any] | None
) -> dict[str, set[str]]:
    """Group record ids referenced by link-field filter conditions by foreign table."""
    result: dict[str, set[str]] = {}
    if not filter_obj or not filter_obj.get("filterSet"):
        return result
    for item in filter_obj["filterSet"]:
        if "filterSet" in item:
            for ft_id, ids in _collect_filter_link_records(link_fields, item).items():
                result.setdefault(ft_id, set()).update(ids)
            continue
        foreign_table_id = link_fields.get(item.get("fieldId"))
        if not foreign_table_id:
            continue
        value = item.get("value")
        if isinstance(value, list):
            result.setdefault(foreign_table_id, set()).update(
                v for v in value if isinstance(v, str)
            )
        elif isinstance(value, str) and value.startswith("rec"):
            result.setdefault(foreign_table_id, set()).add(value)
    return result


async def get_filter_link_records_by_table(
    table_id: str, filter_obj: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """Ports view-open-api getFilterLinkRecordsByTable.

    Returns the foreign records referenced by link-field conditions in the
    filter, grouped by foreign table; ``[]`` when the filter references none.
    """
    if not filter_obj:
        return []
    from sqlalchemy import text

    from ...db import engine as db_engine
    from ...db.provider import parse_db_table_name

    fields = await table_repository.list_field_rows(table_id)
    link_field_table: dict[str, str] = {}
    lookup_by_foreign: dict[str, str] = {}
    for field in fields:
        if field["type"] != "link" or field.get("is_lookup"):
            continue
        options = json.loads(field["options"] or "{}")
        foreign_table_id = options.get("foreignTableId")
        if foreign_table_id:
            link_field_table[field["id"]] = foreign_table_id
            if options.get("lookupFieldId"):
                lookup_by_foreign[foreign_table_id] = options["lookupFieldId"]

    table_record_map = _collect_filter_link_records(link_field_table, filter_obj)
    if not table_record_map:
        return []

    result: list[dict[str, Any]] = []
    for foreign_table_id, record_ids in table_record_map.items():
        lookup_field_id = lookup_by_foreign.get(foreign_table_id)
        if not lookup_field_id:
            continue
        foreign_fields = await table_repository.list_field_rows(foreign_table_id)
        lookup = next((f for f in foreign_fields if f["id"] == lookup_field_id), None)
        foreign_table = await get_table_meta_by_id(foreign_table_id)
        if lookup is None or foreign_table is None:
            continue
        schema, table = parse_db_table_name(foreign_table["db_table_name"])
        sql = text(
            f'SELECT "__id" AS id, "{lookup["db_field_name"]}" AS title '
            f'FROM "{schema}"."{table}" WHERE "__id" = ANY(:ids) ORDER BY "__auto_number"'
        )
        async with db_engine.session() as session:
            rows = (await session.execute(sql, {"ids": list(record_ids)})).mappings().all()
        result.append(
            {
                "tableId": foreign_table_id,
                "records": [
                    {"id": r["id"], "title": r["title"] if r["title"] else None}
                    for r in rows
                ],
            }
        )
    return result


def _invalid_view_id() -> ApiError:
    return ApiError(
        "Invalid ViewId",
        HttpErrorCode.VALIDATION_ERROR,
        {"domainCode": "validation.invalid", "domainTags": ["validation"]},
    )


def _view_not_found() -> ApiError:
    return ApiError(
        "View not found",
        HttpErrorCode.NOT_FOUND,
        {"localization": {"i18nKey": "httpErrors.view.notFound"}},
    )


def _cannot_delete_last_view() -> ApiError:
    return ApiError(
        "Cannot delete the last view in a table. A table must have at least one view.",
        HttpErrorCode.VALIDATION_ERROR,
        {"localization": {"i18nKey": "httpErrors.view.cannotDeleteLastView"}},
    )


def _view_vo(row: dict[str, Any]) -> dict[str, Any]:
    vo: dict[str, Any] = {"id": row["id"], "name": row["name"], "type": row["type"]}
    if row.get("description") is not None:
        vo["description"] = row["description"]
    for key in JSON_KEYS:
        raw = row.get(key)
        if raw is not None:
            vo[key] = json.loads(raw)
    if row.get("is_locked"):
        vo["isLocked"] = True
    if row.get("share_id"):
        vo["shareId"] = row["share_id"]
    if row.get("enable_share"):
        vo["enableShare"] = True
    if row.get("share_meta") is not None:
        vo["shareMeta"] = json.loads(row["share_meta"])
    vo["createdBy"] = row["created_by"]
    if row.get("last_modified_by") is not None:
        vo["lastModifiedBy"] = row["last_modified_by"]
    vo["createdTime"] = _iso(row["created_time"])
    if row.get("last_modified_time") is not None:
        vo["lastModifiedTime"] = _iso(row["last_modified_time"])
    vo["columnMeta"] = json.loads(row["column_meta"] or "{}")
    return vo


class ViewService:
    async def _load_table(self, table_id: str) -> dict[str, Any]:
        table = await get_table_meta_by_id(table_id)
        if table is None or table["deleted_time"] is not None:
            raise ApiError(
                "Table not found",
                HttpErrorCode.NOT_FOUND,
                {"domainCode": "table.not_found", "domainTags": ["not-found"]},
            )
        return table

    async def _load_view(self, table_id: str, view_id: str) -> dict[str, Any]:
        view = await repository.get_view_row(table_id, view_id)
        if view is None:
            raise _view_not_found()
        return view

    def _touch(self, view: dict[str, Any]) -> dict[str, Any]:
        return {
            "version": view["version"] + 1,
            "last_modified_time": datetime.now(UTC).replace(tzinfo=None),
            "last_modified_by": cls.get("user.id"),
        }

    async def _write_view(
        self, table_id: str, view: dict[str, Any], updates: dict[str, Any]
    ) -> dict[str, Any]:
        old_vo = _view_vo(view)
        new_row = await repository.update_view_row(view["id"], updates) or view
        from ...realtime.broadcast import broadcast_view_update, build_set_property_ops

        ops = build_set_property_ops(
            old_vo, _view_vo(new_row), ignore=("lastModifiedTime", "lastModifiedBy")
        )
        await broadcast_view_update(table_id, view["id"], ops, new_row["version"])
        return new_row

    async def create_view(self, table_id: str, body: ViewCreateBody) -> dict[str, Any]:
        await self._load_table(table_id)
        user_id = cls.get("user.id")

        if body.columnMeta is not None:
            column_meta = body.columnMeta
        else:
            fields = await table_repository.list_field_rows(table_id)
            column_meta = {f["id"]: {"order": i} for i, f in enumerate(fields)}
            if body.type == "kanban" and fields:
                first = column_meta[fields[0]["id"]]
                first["order"] = first.get("order", 0)
                first["visible"] = True
            if body.type == "form":
                for meta in column_meta.values():
                    meta["visible"] = True
        row = await table_repository.insert_view_row(
            {
                "id": new_id(IdPrefix.VIEW),
                "name": body.name or "Grid view",
                "description": body.description,
                "table_id": table_id,
                "type": body.type,
                "order": body.order
                if body.order is not None
                else await repository.max_view_order(table_id) + 1,
                "version": 1,
                "column_meta": _dump(column_meta) or "{}",
                "sort": _dump(body.sort),
                "filter": _dump(body.filter),
                "group": _dump(body.group),
                "options": _dump(body.options),
                "is_locked": body.isLocked,
                "created_by": user_id,
            }
        )
        vo = _view_vo(row)
        from ...realtime.broadcast import broadcast_view_create
        from ..undo_redo.stack import capture_operation

        await broadcast_view_create(table_id, vo)
        await capture_operation(
            table_id,
            {"name": "createView", "params": {"tableId": table_id}, "result": {"view": vo}},
        )
        return vo

    async def get_view(self, table_id: str, view_id: str) -> dict[str, Any]:
        await self._load_table(table_id)
        return _view_vo(await self._load_view(table_id, view_id))

    async def get_filter_link_records(
        self, table_id: str, view_id: str
    ) -> list[dict[str, Any]]:
        await self._load_table(table_id)
        view = await self._load_view(table_id, view_id)
        filter_obj = json.loads(view["filter"]) if view.get("filter") else None
        return await get_filter_link_records_by_table(table_id, filter_obj)

    async def list_views(self, table_id: str) -> list[dict[str, Any]]:
        await self._load_table(table_id)
        return [_view_vo(r) for r in await table_repository.list_view_rows(table_id)]

    # ---- realtime socket snapshots -----------------------------------------

    async def socket_snapshot_bulk(
        self, table_id: str, ids: list[str]
    ) -> list[dict[str, Any]]:
        """Return ShareDB view snapshots ``{id, v, type, data}`` for ids."""
        await self._load_table(table_id)
        rows = {r["id"]: r for r in await table_repository.list_view_rows(table_id)}
        snapshots: list[dict[str, Any]] = []
        for view_id in ids:
            row = rows.get(view_id)
            if row is None:
                continue
            snapshots.append(
                {
                    "id": view_id,
                    "v": row["version"],
                    "type": "json0",
                    "data": _view_vo(row),
                }
            )
        return snapshots

    async def socket_doc_ids(
        self, table_id: str, query: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        await self._load_table(table_id)
        rows = await table_repository.list_view_rows(table_id)
        return {"ids": [r["id"] for r in rows]}

    async def update_name(self, table_id: str, view_id: str, body: ViewNameBody) -> None:
        view = await self._load_view(table_id, view_id)
        await self._write_view(table_id, view, {"name": body.name, **self._touch(view)})

    async def update_description(
        self, table_id: str, view_id: str, description: str | None
    ) -> None:
        view = await self._load_view(table_id, view_id)
        await self._write_view(
            table_id, view, {"description": description, **self._touch(view)}
        )

    async def update_locked(
        self, table_id: str, view_id: str, is_locked: bool | None
    ) -> None:
        view = await self._load_view(table_id, view_id)
        await self._write_view(table_id, view, {"is_locked": is_locked, **self._touch(view)})

    async def update_json_prop(
        self, table_id: str, view_id: str, key: str, value: Any
    ) -> None:
        view = await self._load_view(table_id, view_id)
        old_value = json.loads(view[key]) if view.get(key) else None
        await self._write_view(table_id, view, {key: _dump(value), **self._touch(view)})
        action_key = {"filter": "applyViewFilter", "group": "applyViewGroup"}.get(key)
        if action_key and value:
            from ...realtime.broadcast import broadcast_action_trigger

            await broadcast_action_trigger(view_id, [{"actionKey": action_key}])
        from ..undo_redo.stack import capture_operation

        await capture_operation(
            table_id,
            {
                "name": "updateView",
                "params": {"tableId": table_id, "viewId": view_id},
                "result": {"byKey": {"key": key, "oldValue": old_value, "newValue": value}},
            },
        )

    async def update_options(
        self, table_id: str, view_id: str, options: dict[str, Any] | None
    ) -> None:
        view = await self._load_view(table_id, view_id)
        old = json.loads(view["options"]) if view.get("options") else {}
        merged = {**old, **(options or {})}
        await self._write_view(table_id, view, {"options": _dump(merged), **self._touch(view)})

    async def update_column_meta(
        self, table_id: str, view_id: str, items: list[ColumnMetaItem]
    ) -> None:
        view = await self._load_view(table_id, view_id)
        column_meta = json.loads(view["column_meta"] or "{}")
        buffer: list[str] = []
        for item in items:
            old = column_meta.get(item.fieldId, {})
            merged = {**old, **item.columnMeta}
            column_meta[item.fieldId] = merged
            new = item.columnMeta
            if "hidden" in new and not new.get("hidden") and old.get("hidden") != new.get("hidden"):
                buffer.append("showViewField")
            if "statisticFunc" in new and old.get("statisticFunc") != new.get("statisticFunc"):
                buffer.append("applyViewStatisticFunc")
        await self._write_view(
            table_id, view, {"column_meta": _dump(column_meta), **self._touch(view)}
        )
        if buffer:
            from ...realtime.broadcast import broadcast_action_trigger

            # de-duplicate while preserving order (a batch may repeat action keys)
            seen: dict[str, None] = dict.fromkeys(buffer)
            await broadcast_action_trigger(
                view_id, [{"actionKey": key} for key in seen]
            )

    async def update_share_meta(
        self, table_id: str, view_id: str, body: ShareMetaBody
    ) -> None:
        view = await self._load_view(table_id, view_id)
        # overwrite semantics; a {"shareMeta": ...} wrapper degrades to {}
        # (ZodModel extra=ignore), matching ref strip behaviour.
        meta = body.model_dump(exclude_none=True)
        await self._write_view(
            table_id, view, {"share_meta": _dump(meta), **self._touch(view)}
        )

    async def enable_share(self, table_id: str, view_id: str) -> dict[str, Any]:
        view = await self._load_view(table_id, view_id)
        share_meta = (
            json.loads(view["share_meta"]) if view.get("share_meta") else {"includeRecords": True}
        )
        row = await self._write_view(
            table_id,
            view,
            {
                "share_id": new_id(IdPrefix.SHARE),
                "enable_share": True,
                "share_meta": _dump(share_meta),
                **self._touch(view),
            },
        )
        return {"shareId": row["share_id"] if row else None}

    async def refresh_share_id(self, table_id: str, view_id: str) -> dict[str, Any]:
        view = await self._load_view(table_id, view_id)
        row = await self._write_view(
            table_id, view, {"share_id": new_id(IdPrefix.SHARE), **self._touch(view)}
        )
        return {"shareId": row["share_id"] if row else None}

    async def disable_share(self, table_id: str, view_id: str) -> None:
        view = await self._load_view(table_id, view_id)
        await self._write_view(table_id, view, {"enable_share": None, **self._touch(view)})

    async def update_order(
        self, table_id: str, view_id: str, anchor_id: str, position: str
    ) -> None:
        view = await self._load_view(table_id, view_id)
        anchor = await repository.get_view_row(table_id, anchor_id)
        if anchor is None:
            raise _invalid_view_id()
        below = position == "after"
        neighbor = await repository.list_next_view_by_order(
            table_id, anchor["order"], below=not below
        )
        if neighbor is None:
            new_order: float = anchor["order"] + (1 if below else -1)
        else:
            new_order = (neighbor["order"] + anchor["order"]) / 2
        await self._write_view(table_id, view, {"order": new_order, **self._touch(view)})

    async def update_record_order(
        self,
        table_id: str,
        view_id: str,
        anchor_id: str,
        position: str,
        record_ids: list[str],
    ) -> None:
        table = await self._load_table(table_id)
        await self._load_view(table_id, view_id)
        base_id = table["base_id"]
        if not await repository.record_exists(base_id, table_id, anchor_id):
            raise ApiError(
                f"Anchor record not found: {anchor_id}",
                HttpErrorCode.NOT_FOUND,
                {"domainCode": "record.not_found", "domainTags": ["not-found"]},
            )
        if not record_ids:
            return
        column = f"__row_{view_id}"
        await record_repository.ensure_view_order_column(base_id, table_id, column)
        anchor_value = await record_repository.get_column_value(
            base_id, table_id, column, anchor_id
        )
        if anchor_value is None:
            return
        below = position == "after"
        neighbor = await record_repository.neighbor_order_value(
            base_id, table_id, column, anchor_value, below, [*record_ids, anchor_id]
        )
        old_orders = {
            rid: await record_repository.get_column_value(base_id, table_id, column, rid)
            for rid in record_ids
        }
        count = len(record_ids)
        if neighbor is None:
            # anchor sits at the edge on this side; lay the moved rows out just
            # beyond it, preserving their given order.
            if below:
                orders = {rid: anchor_value + (i + 1) for i, rid in enumerate(record_ids)}
            else:
                orders = {
                    rid: anchor_value - (count - i) for i, rid in enumerate(record_ids)
                }
        else:
            lo, hi = (anchor_value, neighbor) if below else (neighbor, anchor_value)
            gap = (hi - lo) / (count + 1)
            orders = {rid: lo + gap * (i + 1) for i, rid in enumerate(record_ids)}
        await record_repository.set_column_values(base_id, table_id, column, orders)
        from ...realtime.broadcast import broadcast_action_trigger
        from ..undo_redo.stack import capture_operation

        # empty fieldIds marks a row reorder: field-aware listeners skip, but the
        # grid still re-queries to pick up the new order.
        await broadcast_action_trigger(
            table_id, [{"actionKey": "setRecord", "payload": {"fieldIds": []}}]
        )
        orders_map = {
            rid: {"oldOrder": {view_id: old_orders.get(rid)}, "newOrder": {view_id: orders[rid]}}
            for rid in record_ids
        }
        await capture_operation(
            table_id,
            {
                "name": "updateRecordsOrder",
                "params": {"tableId": table_id, "viewId": view_id, "recordIds": record_ids},
                "result": {"ordersMap": orders_map},
            },
        )

    async def restore_record_orders(
        self, table_id: str, view_id: str, orders: dict[str, float]
    ) -> None:
        """Replay row-order values for undo/redo of a manual reorder."""
        table = await self._load_table(table_id)
        column = f"__row_{view_id}"
        await record_repository.ensure_view_order_column(table["base_id"], table_id, column)
        await record_repository.set_column_values(table["base_id"], table_id, column, orders)
        from ...realtime.broadcast import broadcast_action_trigger

        await broadcast_action_trigger(
            table_id, [{"actionKey": "setRecord", "payload": {"fieldIds": []}}]
        )

    async def duplicate_view(self, table_id: str, view_id: str) -> dict[str, Any]:
        await self._load_table(table_id)
        source = await self._load_view(table_id, view_id)
        user_id = cls.get("user.id")
        row = await table_repository.insert_view_row(
            {
                "id": new_id(IdPrefix.VIEW),
                "name": f"{source['name']} 2",
                "description": source["description"],
                "table_id": table_id,
                "type": source["type"],
                "order": await repository.max_view_order(table_id) + 1,
                "version": 1,
                "column_meta": source["column_meta"],
                "sort": source["sort"],
                "filter": source["filter"],
                "group": source["group"],
                "options": source["options"],
                "is_locked": source["is_locked"],
                "share_id": new_id(IdPrefix.SHARE),
                "share_meta": source["share_meta"],
                "created_by": user_id,
            }
        )
        vo = _view_vo(row)
        from ...realtime.broadcast import broadcast_view_create

        await broadcast_view_create(table_id, vo)
        return vo

    async def delete_view(self, table_id: str, view_id: str) -> None:
        await self._load_table(table_id)
        views = await table_repository.list_view_rows(table_id)
        if len(views) <= 1:
            raise _cannot_delete_last_view()
        target = next((v for v in views if v["id"] == view_id), None)
        if target is None:
            raise _view_not_found()
        await repository.soft_delete_view_row(
            view_id, datetime.now(UTC).replace(tzinfo=None), target["version"] + 1
        )
        from ...realtime.broadcast import broadcast_view_delete
        from ..undo_redo.stack import capture_operation

        await broadcast_view_delete(table_id, view_id, target["version"])
        await capture_operation(
            table_id,
            {"name": "deleteView", "params": {"tableId": table_id, "viewId": view_id}},
        )

    async def restore_view(self, table_id: str, view_id: str) -> dict[str, Any] | None:
        """Undo of a view delete: clear the soft-delete tombstone and re-announce."""
        view = await repository.get_view_row(table_id, view_id, include_deleted=True)
        if view is None:
            return None
        row = await repository.update_view_row(
            view_id,
            {"deleted_time": None, **self._touch(view)},
        )
        vo = _view_vo(row or view)
        from ...realtime.broadcast import broadcast_view_create

        await broadcast_view_create(table_id, vo)
        return vo
