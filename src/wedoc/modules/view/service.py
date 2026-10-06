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
from ...core.ids import IdPrefix, is_valid_prefixed_id, new_id
from ..field.repository import get_table_meta_by_id
from ..record import repository as record_repository
from ..table import repository as table_repository
from . import repository
from .schemas import (
    ColumnMetaItem,
    ShareMetaBody,
    ViewCreateBody,
    ViewNameBody,
    validate_view_options,
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


def _order_column_meta(fields: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    # generateViewOrderColumnMeta: [isPrimary asc nulls last, order asc, createdTime asc]
    ordered = sorted(
        fields,
        key=lambda f: (
            0 if f.get("is_primary") else 1,
            f["order"],
            f["created_time"],
        ),
    )
    return {f["id"]: {"order": i} for i, f in enumerate(ordered)}


def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {k: dict(v) if isinstance(v, dict) else v for k, v in base.items()}
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _view_data_compensation(
    view_type: str,
    fields: list[dict[str, Any]],
    column_meta: dict[str, Any],
    options: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    # viewDataCompensation: primary-field visibility + type-specific option defaults.
    column_meta = {k: dict(v) if isinstance(v, dict) else v for k, v in column_meta.items()}
    if view_type in ("kanban", "gallery", "calendar"):
        primary = next((f for f in fields if f.get("is_primary")), None)
        if primary is not None:
            prev = dict(column_meta.get(primary["id"]) or {})
            prev["visible"] = True
            column_meta[primary["id"]] = prev
        if view_type == "gallery":
            gallery_opts = dict(options or {})
            cover = gallery_opts.get("coverFieldId")
            if cover is None:
                cover = next(
                    (f["id"] for f in fields if f.get("type") == "attachment"), None
                )
            if cover is not None:
                gallery_opts["coverFieldId"] = cover
            options = gallery_opts
        elif view_type == "calendar":
            date_field_ids = [
                f["id"]
                for f in fields
                if f.get("cell_value_type") == "dateTime"
                and not f.get("is_multiple_cell_value")
            ]
            if date_field_ids:
                cal_opts = dict(options or {})
                cal_opts["startDateFieldId"] = (
                    cal_opts.get("startDateFieldId") or date_field_ids[0]
                )
                cal_opts["endDateFieldId"] = cal_opts.get("endDateFieldId") or (
                    date_field_ids[1] if len(date_field_ids) > 1 else date_field_ids[0]
                )
                options = cal_opts
    if view_type == "form":
        for f in fields:
            if f.get("is_computed") or f.get("type") == "button":
                continue
            prev = dict(column_meta.get(f["id"]) or {})
            prev["visible"] = True
            column_meta[f["id"]] = prev
    return column_meta, options


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


def _anchor_not_found(anchor_id: str, table_id: str) -> ApiError:
    return ApiError(
        f"Anchor not found with id: {anchor_id} and tableId: {table_id}",
        HttpErrorCode.NOT_FOUND,
        {"localization": {"i18nKey": "httpErrors.view.anchorNotFound"}},
    )


def _extract_filter_field_ids(filter_obj: Any) -> list[str]:
    ids: list[str] = []
    if not isinstance(filter_obj, dict):
        return ids
    for item in filter_obj.get("filterSet") or []:
        if not isinstance(item, dict):
            continue
        if "filterSet" in item:
            ids.extend(_extract_filter_field_ids(item))
        elif "fieldId" in item:
            ids.append(item["fieldId"])
    return ids


async def _validate_view_property(table_id: str, key: str, value: Any) -> None:
    if value is None:
        return
    if key == "filter":
        field_ids = _extract_filter_field_ids(value)
        label, i18n_key = "Filter", "httpErrors.view.filterUnsupportedFieldType"
    elif key == "sort":
        objs = value.get("sortObjs") if isinstance(value, dict) else None
        field_ids = [o.get("fieldId") for o in objs or [] if isinstance(o, dict)]
        label, i18n_key = "Sort", "httpErrors.view.sortUnsupportedFieldType"
    elif key == "group":
        field_ids = [o.get("fieldId") for o in value or [] if isinstance(o, dict)]
        label, i18n_key = "Group", "httpErrors.view.groupUnsupportedFieldType"
    else:
        return
    referenced = {fid for fid in field_ids if fid}
    if not referenced:
        return
    fields = await table_repository.list_field_rows(table_id)
    unsupported = [f["id"] for f in fields if f["id"] in referenced and f["type"] == "button"]
    if unsupported:
        raise ApiError(
            f"{label} fields {', '.join(unsupported)} are unsupported button type fields",
            HttpErrorCode.VALIDATION_ERROR,
            {"localization": {"i18nKey": i18n_key}},
        )


def _view_not_found(view_id: str | None = None) -> ApiError:
    return ApiError(
        f"View not found: {view_id}" if view_id else "View not found",
        HttpErrorCode.NOT_FOUND,
        {"domainCode": "view.not_found", "domainTags": ["not-found"]},
    )


def _require_view_id(view_id: str) -> None:
    if not is_valid_prefixed_id(view_id, "viw"):
        raise ApiError(
            "Invalid ViewId",
            HttpErrorCode.VALIDATION_ERROR,
            {"domainCode": "validation.invalid", "domainTags": ["validation"]},
        )


def _cannot_delete_last_view() -> ApiError:
    return ApiError(
        "Cannot delete the last view in a table. A table must have at least one view.",
        HttpErrorCode.VALIDATION_ERROR,
        {"domainCode": "view.cannot_delete_last", "domainTags": ["validation"]},
    )


def _view_vo(row: dict[str, Any]) -> dict[str, Any]:
    vo: dict[str, Any] = {"id": row["id"], "name": row["name"], "type": row["type"]}
    order = row["order"]
    vo["order"] = int(order) if float(order).is_integer() else order
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
        _require_view_id(view_id)
        view = await repository.get_view_row(table_id, view_id)
        if view is None:
            raise _view_not_found(view_id)
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

    async def create_view(
        self, table_id: str, body: ViewCreateBody, *, install_plugin: bool = True
    ) -> dict[str, Any]:
        await self._load_table(table_id)
        if body.type != "plugin" and body.options is not None:
            validate_view_options(body.type, body.options)
        if body.type == "plugin" and install_plugin:
            # createView delegates plugin views to the plugin-install path, then
            # returns the created view VO (view-open-api.service.createView).
            from .plugin_service import ViewPluginService

            options = body.options or {}
            res = await ViewPluginService().install(
                table_id, {"name": body.name, "pluginId": options.get("pluginId")}
            )
            return await self.get_view(table_id, res["viewId"])
        user_id = cls.get("user.id")
        now = datetime.now(UTC).replace(tzinfo=None)

        fields = await table_repository.list_field_rows(table_id)
        order_column_meta = _order_column_meta(fields)
        column_meta = (
            _deep_merge(order_column_meta, body.columnMeta)
            if body.columnMeta is not None
            else order_column_meta
        )
        column_meta, options = _view_data_compensation(
            body.type, fields, column_meta, body.options
        )
        active_ids = {f["id"] for f in fields}
        column_meta = {k: v for k, v in column_meta.items() if k in active_ids}
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
                "options": _dump(options),
                "is_locked": body.isLocked,
                "created_time": now,
                "created_by": user_id,
                "last_modified_time": now,
                "last_modified_by": user_id,
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
        self, table_id: str, view_id: str, description: str
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
        self, table_id: str, view_id: str, key: str, value: Any, *, validate: bool = True
    ) -> None:
        view = await self._load_view(table_id, view_id)
        if validate:
            await _validate_view_property(table_id, key, value)
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
        validate_view_options(view["type"], options or {})
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

    async def add_field_to_column_meta(self, table_id: str, field_id: str) -> None:
        """Port view.service.initViewColumnMeta for a single freshly created
        field: append it to every view's columnMeta at (max existing order + 1),
        matching the reference where empty columnMeta yields order 0."""
        for view in await table_repository.list_view_rows(table_id):
            column_meta = json.loads(view["column_meta"] or "{}")
            orders = [
                meta["order"]
                for meta in column_meta.values()
                if isinstance(meta, dict) and isinstance(meta.get("order"), int | float)
            ]
            max_order = max(orders) if orders else -1
            column_meta[field_id] = {"order": max_order + 1}
            await self._write_view(
                table_id, view, {"column_meta": _dump(column_meta), **self._touch(view)}
            )

    async def add_duplicated_field_to_column_meta(
        self, table_id: str, field_id: str, source_field_id: str, view_id: str | None
    ) -> None:
        """Port field-open-api resolveDuplicateFieldOrder + initViewColumnMeta:
        in the originating view the duplicate lands right after its source (midway
        to the next column, or source+1 when it is last); every other view (and
        the originating one when the source has no entry) appends at max order+1."""
        for view in await table_repository.list_view_rows(table_id):
            column_meta = json.loads(view["column_meta"] or "{}")
            orders = [
                meta["order"]
                for meta in column_meta.values()
                if isinstance(meta, dict) and isinstance(meta.get("order"), int | float)
            ]
            max_order = max(orders) if orders else -1
            source_order = None
            if view["id"] == view_id:
                src = column_meta.get(source_field_id)
                if isinstance(src, dict) and isinstance(src.get("order"), int | float):
                    source_order = src["order"]
            if source_order is not None:
                subsequent = sorted(o for o in orders if o > source_order)
                order = (subsequent[0] + source_order) / 2 if subsequent else source_order + 1
            else:
                order = max_order + 1
            column_meta[field_id] = {"order": order}
            await self._write_view(
                table_id, view, {"column_meta": _dump(column_meta), **self._touch(view)}
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
            raise _anchor_not_found(anchor_id, table_id)
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
            raise _anchor_not_found(anchor_id, table_id)
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
        _require_view_id(view_id)
        await self._load_table(table_id)
        views = await table_repository.list_view_rows(table_id)
        target = next((v for v in views if v["id"] == view_id), None)
        if target is None:
            raise _view_not_found(view_id)
        if len(views) <= 1:
            raise _cannot_delete_last_view()
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
