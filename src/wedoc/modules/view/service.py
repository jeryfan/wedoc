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


def _invalid_view_id() -> ApiError:
    return ApiError(
        "Invalid ViewId",
        HttpErrorCode.VALIDATION_ERROR,
        {"domainCode": "validation.invalid", "domainTags": ["validation"]},
    )


def _view_vo(row: dict[str, Any]) -> dict[str, Any]:
    vo: dict[str, Any] = {"id": row["id"], "name": row["name"], "type": row["type"]}
    if row.get("description") is not None:
        vo["description"] = row["description"]
    order = row["order"]
    vo["order"] = int(order) if float(order).is_integer() else order
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
            raise _invalid_view_id()
        return view

    def _touch(self, view: dict[str, Any]) -> dict[str, Any]:
        return {
            "version": view["version"] + 1,
            "last_modified_time": datetime.now(UTC).replace(tzinfo=None),
            "last_modified_by": cls.get("user.id"),
        }

    async def create_view(self, table_id: str, body: ViewCreateBody) -> dict[str, Any]:
        await self._load_table(table_id)
        user_id = cls.get("user.id")
        now = datetime.now(UTC).replace(tzinfo=None)

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
                "last_modified_time": now,
                "last_modified_by": user_id,
            }
        )
        return _view_vo(row)

    async def get_view(self, table_id: str, view_id: str) -> dict[str, Any]:
        await self._load_table(table_id)
        return _view_vo(await self._load_view(table_id, view_id))

    async def list_views(self, table_id: str) -> list[dict[str, Any]]:
        await self._load_table(table_id)
        return [_view_vo(r) for r in await table_repository.list_view_rows(table_id)]

    async def update_name(self, table_id: str, view_id: str, body: ViewNameBody) -> None:
        view = await self._load_view(table_id, view_id)
        await repository.update_view_row(
            view_id, {"name": body.name, **self._touch(view)}
        )

    async def update_description(
        self, table_id: str, view_id: str, description: str | None
    ) -> None:
        view = await self._load_view(table_id, view_id)
        await repository.update_view_row(
            view_id, {"description": description, **self._touch(view)}
        )

    async def update_locked(
        self, table_id: str, view_id: str, is_locked: bool | None
    ) -> None:
        view = await self._load_view(table_id, view_id)
        await repository.update_view_row(
            view_id, {"is_locked": is_locked, **self._touch(view)}
        )

    async def update_json_prop(
        self, table_id: str, view_id: str, key: str, value: Any
    ) -> None:
        view = await self._load_view(table_id, view_id)
        await repository.update_view_row(
            view_id, {key: _dump(value), **self._touch(view)}
        )

    async def update_column_meta(
        self, table_id: str, view_id: str, items: list[ColumnMetaItem]
    ) -> None:
        view = await self._load_view(table_id, view_id)
        column_meta = json.loads(view["column_meta"] or "{}")
        for item in items:
            merged = {**column_meta.get(item.fieldId, {}), **item.columnMeta}
            column_meta[item.fieldId] = merged
        await repository.update_view_row(
            view_id, {"column_meta": _dump(column_meta), **self._touch(view)}
        )

    async def update_share_meta(
        self, table_id: str, view_id: str, body: ShareMetaBody
    ) -> None:
        view = await self._load_view(table_id, view_id)
        # overwrite semantics; a {"shareMeta": ...} wrapper degrades to {}
        # (ZodModel extra=ignore), matching ref strip behaviour.
        meta = body.model_dump(exclude_none=True)
        await repository.update_view_row(
            view_id, {"share_meta": _dump(meta), **self._touch(view)}
        )

    async def enable_share(self, table_id: str, view_id: str) -> dict[str, Any]:
        view = await self._load_view(table_id, view_id)
        share_meta = (
            json.loads(view["share_meta"]) if view.get("share_meta") else {"includeRecords": True}
        )
        row = await repository.update_view_row(
            view_id,
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
        row = await repository.update_view_row(
            view_id, {"share_id": new_id(IdPrefix.SHARE), **self._touch(view)}
        )
        return {"shareId": row["share_id"] if row else None}

    async def disable_share(self, table_id: str, view_id: str) -> None:
        view = await self._load_view(table_id, view_id)
        await repository.update_view_row(
            view_id, {"enable_share": None, **self._touch(view)}
        )

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
        await repository.update_view_row(
            view_id, {"order": new_order, **self._touch(view)}
        )

    async def update_record_order(
        self, table_id: str, view_id: str, anchor_id: str
    ) -> None:
        table = await self._load_table(table_id)
        await self._load_view(table_id, view_id)
        if not await repository.record_exists(table["base_id"], table_id, anchor_id):
            raise ApiError(
                f"Anchor record not found: {anchor_id}",
                HttpErrorCode.NOT_FOUND,
                {"domainCode": "record.not_found", "domainTags": ["not-found"]},
            )

    async def duplicate_view(self, table_id: str, view_id: str) -> dict[str, Any]:
        await self._load_table(table_id)
        source = await self._load_view(table_id, view_id)
        user_id = cls.get("user.id")
        now = datetime.now(UTC).replace(tzinfo=None)
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
                "last_modified_time": now,
                "last_modified_by": user_id,
            }
        )
        return _view_vo(row)

    async def delete_view(self, table_id: str, view_id: str) -> None:
        view = await self._load_view(table_id, view_id)
        await repository.soft_delete_view_row(
            view_id, datetime.now(UTC).replace(tzinfo=None), view["version"] + 1
        )
