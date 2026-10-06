"""Trash domain service — ports trash.service.ts (space/base/table v1 paths).

Scope note: getTableTrashItemRecords (record pagination + snapshot
normalization), the reset-items table branch and operation-id record restore
still depend on parts of the v2 record-removal pipeline not yet in wedoc, and
stay deferred (documented in the ledger). The space/base paths, table
restore/permanent-delete, the /trash/items table branch (filters +
view/field/record resourceMap) and the restore-field SSE stream (field-only,
terminal-frame approximation of the v2 progression) are ported here.
"""

import json
from typing import Any

from ...core import cls
from ...core.errors import ApiError, HttpErrorCode
from ...core.storage import get_public_full_storage_url
from . import repository
from .repository import _iso

_TABLE_TRASH_PREVIEW_LIMIT = 20


def _invalid_resource_type(resource_type: str) -> ApiError:
    return ApiError(
        f"Invalid resource type {resource_type}",
        HttpErrorCode.VALIDATION_ERROR,
        {"localization": {"i18nKey": "httpErrors.trash.invalidResourceType"}},
    )


def _trash_not_found(trash_id: str) -> ApiError:
    return ApiError(
        f"The trash {trash_id} not found",
        HttpErrorCode.NOT_FOUND,
        {"localization": {"i18nKey": "httpErrors.trash.notFound"}},
    )


class TrashService:
    def _user_map(self, users: list[dict[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for u in users:
            out[u["id"]] = {
                "id": u["id"],
                "name": u["name"],
                "email": u["email"],
                "avatar": get_public_full_storage_url(u["avatar"]) if u["avatar"] else None,
            }
        return out

    async def get_trash(self, resource_type: str, space_id: str | None) -> dict[str, Any]:
        if resource_type == "space":
            return await self._get_space_trash()
        if resource_type == "base":
            return await self._get_base_trash(space_id)
        raise _invalid_resource_type(resource_type)

    async def _get_space_trash(self) -> dict[str, Any]:
        user_id = cls.get("user.id")
        space_ids, _ = await repository.authorized_resources(user_id)
        spaces = await repository.list_spaces(space_ids)
        space_map = {s["id"]: s for s in spaces}
        rows = await repository.list_trash_by_resource_ids(
            [s["id"] for s in spaces]
        )
        trash_items: list[dict[str, Any]] = []
        resource_map: dict[str, Any] = {}
        deleted_by: set[str] = set()
        for item in rows:
            if item["resourceType"] != "space" or item["resourceId"] not in space_map:
                continue
            trash_items.append(
                {
                    "id": item["id"],
                    "resourceId": item["resourceId"],
                    "resourceType": item["resourceType"],
                    "deletedTime": _iso(item["deletedTime"]),
                    "deletedBy": item["deletedBy"],
                }
            )
            s = space_map[item["resourceId"]]
            resource_map[item["resourceId"]] = {
                "id": item["resourceId"],
                "name": s["name"],
                "avatar": get_public_full_storage_url(s["avatar"]) if s["avatar"] else None,
            }
            deleted_by.add(item["deletedBy"])
        users = await repository.user_info_list(list(deleted_by))
        return {
            "trashItems": trash_items,
            "resourceMap": resource_map,
            "userMap": self._user_map(users),
            "nextCursor": None,
        }

    async def _get_base_trash(self, space_id: str | None) -> dict[str, Any]:
        user_id = cls.get("user.id")
        space_ids, base_ids = await repository.authorized_resources(user_id)
        bases = await repository.list_bases(space_ids, base_ids)
        base_map = {b["id"]: b for b in bases}
        authorized_base_ids = [b["id"] for b in bases]
        authorized_base_space_ids = [b["spaceId"] for b in bases]

        trashed_space_ids = await repository.list_trashed_space_ids(
            authorized_base_space_ids
        )
        rows = await repository.list_base_trash(
            authorized_base_ids,
            [space_id] if space_id else None,
            trashed_space_ids,
        )
        trash_items: list[dict[str, Any]] = []
        resource_map: dict[str, Any] = {}
        deleted_by: set[str] = set()
        for item in rows:
            if item["resourceId"] not in base_map:
                continue
            trash_items.append(
                {
                    "id": item["id"],
                    "resourceId": item["resourceId"],
                    "resourceType": item["resourceType"],
                    "deletedTime": _iso(item["deletedTime"]),
                    "deletedBy": item["deletedBy"],
                }
            )
            deleted_by.add(item["deletedBy"])
            base_info = base_map[item["resourceId"]]
            resource_map[item["resourceId"]] = {
                "id": item["resourceId"],
                "spaceId": base_info["spaceId"],
                "name": base_info["name"],
            }
            resource_map[base_info["spaceId"]] = {
                "id": base_info["spaceId"],
                "name": base_info["spaceName"],
            }
        users = await repository.user_info_list(list(deleted_by))
        return {
            "trashItems": trash_items,
            "resourceMap": resource_map,
            "userMap": self._user_map(users),
            "nextCursor": None,
        }

    async def get_trash_items(
        self,
        resource_id: str,
        resource_type: str,
        cursor: str | None,
        page_size: int,
        resource_types: list[str] | None,
        deleted_by: list[str] | None,
        deleted_time_start: str | None,
        deleted_time_end: str | None,
    ) -> dict[str, Any]:
        if resource_type == "base":
            return await self._get_base_trash_items(resource_id, cursor, page_size)
        if resource_type == "table":
            return await self._get_table_trash_items(
                resource_id,
                cursor,
                page_size,
                resource_types,
                deleted_by,
                deleted_time_start,
                deleted_time_end,
            )
        raise _invalid_resource_type(resource_type)

    async def _get_table_trash_items(
        self,
        table_id: str,
        cursor: str | None,
        page_size: int,
        resource_types: list[str] | None,
        deleted_by: list[str] | None,
        deleted_time_start: str | None,
        deleted_time_end: str | None,
    ) -> dict[str, Any]:
        from ...core.security.permissions import PermissionService

        await PermissionService().valid_permissions(
            table_id, ["table|trash_read"], cls.get("accessTokenId"), True
        )
        rows = await repository.list_table_trash(
            table_id,
            cursor,
            page_size + 1,
            resource_types,
            deleted_by,
            deleted_time_start,
            deleted_time_end,
        )
        next_cursor = None
        if len(rows) > page_size:
            next_cursor = rows.pop()["id"]
        trash_items: list[dict[str, Any]] = []
        deleted_by_set: set[str] = set()
        preview_ids: dict[str, list[str]] = {"view": [], "field": [], "record": []}
        for item in rows:
            snapshot = json.loads(item["snapshot"])
            resource_type = item["resourceType"]
            if resource_type == "field":
                resource_ids = [f["id"] for f in snapshot.get("fields", [])]
            else:
                resource_ids = snapshot
            preview = resource_ids[:_TABLE_TRASH_PREVIEW_LIMIT]
            trash_items.append(
                {
                    "id": item["id"],
                    "resourceType": resource_type,
                    "deletedTime": _iso(item["createdTime"]),
                    "deletedBy": item["createdBy"],
                    "resourceIds": preview,
                    "totalResourceCount": len(resource_ids),
                }
            )
            deleted_by_set.add(item["createdBy"])
            if resource_type in preview_ids:
                preview_ids[resource_type].extend(preview)
        resource_map: dict[str, Any] = {}
        for view in await repository.list_deleted_views(preview_ids["view"]):
            resource_map[view["id"]] = {
                "id": view["id"],
                "name": view["name"],
                "type": view["type"],
            }
        for field in await repository.list_deleted_fields(preview_ids["field"]):
            entry: dict[str, Any] = {
                "id": field["id"],
                "name": field["name"],
                "type": field["type"],
            }
            if field["options"]:
                options = json.loads(field["options"])
                # Select choices live in a separate store, so the trash listing's
                # bare field-options read never carries them; other option keys stay.
                options.pop("choices", None)
                entry["options"] = options
            entry["isLookup"] = field["isLookup"]
            entry["isConditionalLookup"] = field["isConditionalLookup"]
            resource_map[field["id"]] = entry
        for rt in await repository.list_record_trash(table_id, preview_ids["record"]):
            snap = json.loads(rt["snapshot"])
            resource_map[rt["recordId"]] = {"id": rt["recordId"], "name": snap.get("name")}
        users = await repository.user_info_list(list(deleted_by_set))
        # hide record items until every preview id has a materialized snapshot,
        # matching the reference's list/restore race guard (wedoc writes the
        # snapshot synchronously, so ready items are never withheld here).
        ready = [
            item
            for item in trash_items
            if item["resourceType"] != "record"
            or all(rid in resource_map for rid in item["resourceIds"])
        ]
        result: dict[str, Any] = {
            "trashItems": ready,
            "resourceMap": resource_map,
            "userMap": self._user_map(users),
        }
        # the Table branch leaves nextCursor undefined (omitted) when exhausted.
        if next_cursor is not None:
            result["nextCursor"] = next_cursor
        return result

    async def get_table_trash_item_records(
        self, trash_id: str, table_id: str, cursor: str | None, take: int
    ) -> dict[str, Any]:
        from ...core.security.permissions import PermissionService

        await PermissionService().valid_permissions(
            table_id, ["table|trash_read"], cls.get("accessTokenId"), True
        )
        item = await repository.find_table_trash(trash_id, table_id)
        if item is None or item["resourceType"] != "record":
            raise _trash_not_found(trash_id)
        record_ids = json.loads(item["snapshot"])
        trash_rows = await repository.list_record_trash(table_id, record_ids)
        items: list[dict[str, Any]] = []
        deleted_by: set[str] = set()
        for row in trash_rows:
            snap = json.loads(row["snapshot"])
            items.append(
                {
                    "id": row["id"],
                    "recordId": row["recordId"],
                    "record": snap,
                    "deletedTime": _iso(row["createdTime"]),
                    "deletedBy": row["createdBy"],
                    "recordCreatedTime": snap.get("createdTime"),
                    "recordCreatedBy": snap.get("createdBy"),
                    "recordLastModifiedTime": snap.get("lastModifiedTime"),
                    "recordLastModifiedBy": snap.get("lastModifiedBy"),
                }
            )
            deleted_by.add(row["createdBy"])
            if snap.get("createdBy"):
                deleted_by.add(snap["createdBy"])
            if snap.get("lastModifiedBy"):
                deleted_by.add(snap["lastModifiedBy"])
        users = await repository.user_info_list(list(deleted_by))
        return {
            "items": items,
            "userMap": self._user_map(users),
            "nextCursor": None,
        }

    async def restore_field_trash_stream(
        self, trash_id: str, table_id: str | None
    ) -> list[dict[str, Any]]:
        """Restore soft-deleted fields captured under a table_trash 'field' row.

        Returns the SSE frames to emit. The reference streams a v2
        preparing/restoring/done progression while it recomputes record values;
        wedoc's restore_field only clears the tombstone (physical column + data
        retained), so a single terminal 'done' frame is emitted (approximation).
        """
        if not table_id:
            return [
                self._restore_field_error(
                    "preparing", f"Table id is required to restore table trash {trash_id}"
                )
            ]
        item = await repository.find_table_trash(trash_id, table_id)
        if item is None or item["resourceType"] != "field":
            return [
                self._restore_field_error(
                    "preparing", f"The table trash {trash_id} not found"
                )
            ]
        snapshot = json.loads(item["snapshot"])
        field_ids = [f["id"] for f in snapshot.get("fields", [])]
        from ..field.service import FieldService

        service = FieldService()
        for field_id in field_ids:
            # restore_field is a no-op (returns None) for ids already restored or
            # gone, so partial/duplicate restores are handled gracefully.
            await service.restore_field(table_id, field_id)
        await repository.delete_table_trash(trash_id, table_id)
        total_count = await self._table_record_count(table_id)
        return [{"id": "done", "totalCount": total_count, "updatedCount": 0}]

    @staticmethod
    def _restore_field_error(phase: str, message: str) -> dict[str, Any]:
        return {
            "id": "error",
            "phase": phase,
            "batchIndex": -1,
            "totalCount": 0,
            "processedCount": 0,
            "updatedCount": 0,
            "message": message,
        }

    async def _table_record_count(self, table_id: str) -> int:
        from ..field import repository as field_repository

        table = await field_repository.get_table_meta_by_id(table_id, include_deleted=True)
        if table is None:
            return 0
        return await field_repository.count_data_rows(table["base_id"], table_id)

    async def _get_base_trash_items(
        self, base_id: str, cursor: str | None, page_size: int
    ) -> dict[str, Any]:
        from ...core.security.permissions import PermissionService

        await PermissionService().valid_permissions(
            base_id,
            ["table|delete", "app|delete", "automation|delete"],
            cls.get("accessTokenId"),
            True,
        )
        resource_list = await repository.list_trashed_tables(base_id)
        resource_map = {r["id"]: r for r in resource_list}
        rows = await repository.list_trash_by_parent(base_id, cursor, page_size + 1)
        next_cursor = None
        if len(rows) > page_size:
            next_cursor = rows.pop()["id"]
        trash_items: list[dict[str, Any]] = []
        deleted_by: set[str] = set()
        for item in rows:
            trash_items.append(
                {
                    "id": item["id"],
                    "resourceId": item["resourceId"],
                    "resourceType": item["resourceType"],
                    "deletedTime": _iso(item["deletedTime"]),
                    "deletedBy": item["deletedBy"],
                }
            )
            deleted_by.add(item["deletedBy"])
        users = await repository.user_info_list(list(deleted_by))
        return {
            "trashItems": trash_items,
            "resourceMap": resource_map,
            "userMap": self._user_map(users),
            "nextCursor": next_cursor,
        }

    async def restore_trash(self, trash_id: str, table_id: str | None) -> None:
        trash = await repository.find_trash(trash_id)
        if trash is None:
            # record deletions live in table_trash (not the space/base/table trash
            # table) and are addressed with the tableId query param; restore them by
            # re-inserting from the record_trash snapshots.
            if table_id:
                item = await repository.find_table_trash(trash_id, table_id)
                if item is not None and item["resourceType"] == "record":
                    await self._restore_record_trash(trash_id, table_id, item)
                    return
            raise _trash_not_found(trash_id)
        await self._assert_parent_not_trashed(trash["parentId"])
        await self._restore_resource(trash["resourceType"], trash["resourceId"])
        await repository.delete_trash(trash_id)

    async def _restore_record_trash(
        self, trash_id: str, table_id: str, item: dict[str, Any]
    ) -> None:
        from ..record.service import RecordService

        record_ids = json.loads(item["snapshot"])
        snaps: dict[str, dict[str, Any]] = {}
        for row in await repository.list_record_trash(table_id, record_ids):
            snaps.setdefault(row["recordId"], json.loads(row["snapshot"] or "{}"))
        records = [
            {"id": rid, "fields": (snaps.get(rid) or {}).get("fields", {})}
            for rid in record_ids
        ]
        await RecordService().restore_records(table_id, records)
        await repository.delete_table_trash(trash_id, table_id)
        await repository.delete_record_trash(table_id, record_ids)

    async def _restore_resource(self, resource_type: str, resource_id: str) -> None:
        from ...core.security.permissions import PermissionService

        if resource_type == "space":
            await PermissionService().valid_permissions(
                resource_id, ["space|create"], cls.get("accessTokenId"), True
            )
            await repository.restore_space(resource_id)
        elif resource_type == "base":
            await self._restore_base(resource_id)
        elif resource_type == "table":
            await self._restore_table(resource_id)
        else:
            raise _invalid_resource_type(resource_type)

    async def _restore_base(self, base_id: str) -> None:
        from ...core.security.permissions import PermissionService

        await PermissionService().valid_permissions(
            base_id, ["base|create"], cls.get("accessTokenId"), True
        )
        base = await repository.base_of(base_id)
        if base is None:
            raise ApiError(f"The base {base_id} not found", HttpErrorCode.NOT_FOUND)
        trashed_space = await repository.find_trashed_space(base["spaceId"])
        if trashed_space is not None:
            raise ApiError(
                "Unable to restore this base because its parent space is also trashed",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.trash.parentSpaceTrashed"}},
            )
        await repository.restore_base(base_id)

    async def _restore_table(self, table_id: str) -> None:
        from ...core.security.permissions import PermissionService

        await PermissionService().valid_permissions(
            table_id, ["table|create"], cls.get("accessTokenId"), True
        )
        result = await repository.restore_table(table_id)
        if result is None:
            raise ApiError(
                f"The table {table_id} not found",
                HttpErrorCode.NOT_FOUND,
                {"localization": {"i18nKey": "httpErrors.table.notFound"}},
            )

    async def _assert_parent_not_trashed(self, parent_id: str | None) -> None:
        if not parent_id:
            return
        if await repository.parent_chain_trashed(parent_id):
            raise ApiError(
                "Unable to restore this resource because its parent is also in trash",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.trash.parentBaseTrashed"}},
            )

    async def delete(self, trash_id: str) -> None:
        trash = await repository.find_trash(trash_id)
        if trash is None:
            raise _trash_not_found(trash_id)
        await self._delete_resource(
            trash["resourceType"], trash["resourceId"], trash["parentId"]
        )

    async def _delete_resource(
        self, resource_type: str, resource_id: str, parent_id: str | None
    ) -> None:
        if resource_type == "space":
            from ..space.service import SpaceService

            await SpaceService().permanent_delete_space(resource_id)
        elif resource_type == "base":
            from ..base.service import BaseService

            await BaseService().permanent_delete_base(resource_id)
        elif resource_type == "table":
            if not parent_id:
                raise ApiError(
                    "Base ID is required for deleting table resources",
                    HttpErrorCode.VALIDATION_ERROR,
                    {"localization": {"i18nKey": "httpErrors.trash.parentNotFound"}},
                )
            from ...core.security.permissions import PermissionService

            await PermissionService().valid_permissions(
                parent_id, ["table|delete"], cls.get("accessTokenId"), True
            )
            from ..table.service import TableService

            await TableService().permanent_delete_table(parent_id, resource_id)
        else:
            raise _invalid_resource_type(resource_type)

    async def reset_trash_items(self, resource_id: str, resource_type: str) -> None:
        if resource_type not in ("base", "table"):
            raise _invalid_resource_type(resource_type)
        if resource_type == "base":
            await self._reset_base_trash(resource_id)
        elif resource_type == "table":
            # Table-level reset needs the table_trash snapshot pipeline (deferred).
            raise _invalid_resource_type(resource_type)

    async def _reset_base_trash(self, base_id: str) -> None:
        from ...core.security.permissions import PermissionService

        await PermissionService().valid_permissions(
            base_id,
            ["table|delete", "app|delete", "automation|delete"],
            cls.get("accessTokenId"),
            True,
        )
        tables = await repository.list_trashed_tables(base_id)
        if not tables:
            return
        from ..table.service import TableService

        service = TableService()
        for table in tables:
            await service.permanent_delete_table(base_id, table["id"])
