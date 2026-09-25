"""Space domain service — port of features/space/space.service.ts (core surface).

BYODB data-db routes and AI integration management are deliberately out of
scope (single-PG deployment; see AGENTS.md), as is email invitation.
"""

import json
import re
import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete as sa_delete
from sqlalchemy import select

from ...core import cls
from ...core.errors import ApiError, HttpErrorCode
from ...core.ids import IdPrefix, new_id
from ...core.storage import get_public_full_storage_url, get_storage
from ...db import engine as db_engine
from ...db.models_meta import (
    BaseShare,
    Collaborator,
    Integration,
    InvitationRecord,
    Trash,
    UserLastVisit,
)
from ..collaborator.service import RESOURCE_SPACE, CollaboratorService
from ..setting.repository import get_setting
from ..user.repository import upsert_attachment_by_token
from . import repository

SPACE_AVATAR_DIR = "space-avatar"


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def get_uniq_name(name: str, exist_names: list[str]) -> str:
    """Port of the upstream core getUniqName: "Name", "Name 2", "Name 3"..."""
    if name not in exist_names:
        return name
    base_name = name
    num = 2
    try:
        float(name)
    except ValueError:
        match = re.match(r"^(.*)(\b\d+)$", name)
        if match:
            base_name = match.group(1).strip()
            num = int(match.group(2))
    while f"{base_name} {num}" in exist_names:
        num += 1
    return f"{base_name} {num}"


def _not_found() -> ApiError:
    return ApiError(
        "Space not found",
        HttpErrorCode.NOT_FOUND,
        {"localization": {"i18nKey": "httpErrors.space.notFound"}},
    )


def _no_permission() -> ApiError:
    return ApiError(
        "You have no permission to access this space",
        HttpErrorCode.RESTRICTED_RESOURCE,
        {"localization": {"i18nKey": "httpErrors.space.noPermission"}},
    )


class SpaceService:
    def __init__(self) -> None:
        self.collaborators = CollaboratorService()

    # -- create / read ----------------------------------------------------------

    async def create_space(
        self, name: str | None, data_db: dict[str, Any] | None
    ) -> dict[str, Any]:
        user_id = cls.get("user.id")
        is_admin = cls.get("user.isAdmin")
        if not is_admin:
            settings = await get_setting()
            if settings.get("disallowSpaceCreation"):
                raise ApiError(
                    "The current instance disallow space creation by the administrator",
                    HttpErrorCode.RESTRICTED_RESOURCE,
                    {"localization": {"i18nKey": "httpErrors.space.disallowSpaceCreation"}},
                )
        if data_db and data_db.get("mode") == "byodb":
            raise ApiError(
                "BYODB space creation is only available in Enterprise Edition",
                HttpErrorCode.RESTRICTED_RESOURCE,
            )
        existing = await repository.list_space_rows_by_creator(user_id)
        uniq_name = get_uniq_name(name or "Space", [row["name"] for row in existing])
        space_id = new_id(IdPrefix.SPACE)
        space = await repository.insert_space(space_id, uniq_name, user_id)
        await self.collaborators.create_space_collaborator(
            collaborators=[{"principalId": user_id, "principalType": "user"}],
            space_id=space_id,
            role="owner",
            created_by=user_id,
        )
        await self._create_default_ai_integration(space_id)
        return {"id": space["id"], "name": space["name"]}

    @staticmethod
    async def _create_default_ai_integration(space_id: str) -> None:
        now = datetime.now(UTC).replace(tzinfo=None)
        async with db_engine.session() as session:
            await session.execute(
                Integration.__table__.insert().values(
                    id=new_id(IdPrefix.INTEGRATION),
                    resource_id=space_id,
                    type="AI",
                    enable=False,
                    config='{"llmProviders":[]}',
                    created_time=now,
                    last_modified_time=now,
                )
            )
            await session.commit()

    # --- integration CRUD (ports space.service integration methods) ----------

    @staticmethod
    def _integration_vo(row: Any) -> dict[str, Any]:
        vo: dict[str, Any] = {
            "id": row.id,
            "spaceId": row.resource_id,
            "type": row.type,
            "enable": bool(row.enable) if row.enable is not None else False,
            "config": json.loads(row.config),
            "createdTime": _iso(row.created_time),
        }
        if row.last_modified_time is not None:
            vo["lastModifiedTime"] = _iso(row.last_modified_time)
        return vo

    @staticmethod
    def _integration_row_vo(m: Any) -> dict[str, Any]:
        # create/update echo the raw prisma row: resourceId + config as a string.
        # Built from a `.returning()` mapping so it reflects the committed values
        # (the ORM identity map is not refreshed with expire_on_commit=False).
        enable = m["enable"]
        return {
            "id": m["id"],
            "resourceId": m["resource_id"],
            "config": m["config"],
            "type": m["type"],
            "enable": bool(enable) if enable is not None else None,
            "createdTime": _iso(m["created_time"]),
            "lastModifiedTime": _iso(m["last_modified_time"]),
        }

    async def get_integration_list(self, space_id: str) -> list[dict[str, Any]]:
        async with db_engine.session() as session:
            rows = (
                (
                    await session.execute(
                        select(Integration).where(Integration.resource_id == space_id)
                    )
                )
                .scalars()
                .all()
            )
        return [self._integration_vo(row) for row in rows]

    async def create_integration(self, space_id: str, ro: Any) -> dict[str, Any]:
        from .ai_integration import dumps_config, normalize_space_ai_integration_config

        config = ro.config or {}
        now = datetime.now(UTC).replace(tzinfo=None)
        async with db_engine.session() as session:
            existing = (
                await session.execute(
                    select(Integration).where(
                        Integration.resource_id == space_id, Integration.type == "AI"
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                next_config = normalize_space_ai_integration_config(config)
                row_id = new_id(IdPrefix.INTEGRATION)
                created = (
                    (
                        await session.execute(
                            Integration.__table__.insert()
                            .values(
                                id=row_id,
                                resource_id=space_id,
                                type="AI",
                                enable=ro.enable,
                                config=dumps_config(next_config),
                                created_time=now,
                                last_modified_time=now,
                            )
                            .returning(Integration.__table__)
                        )
                    )
                    .mappings()
                    .first()
                )
                await session.commit()
                return self._integration_row_vo(created)

            original = json.loads(existing.config)
            merged = {
                **original,
                **config,
                "llmProviders": [
                    *(original.get("llmProviders") or []),
                    *(config.get("llmProviders") or []),
                ],
            }
            next_config = normalize_space_ai_integration_config(merged)
            updated = (
                (
                    await session.execute(
                        Integration.__table__.update()
                        .where(Integration.id == existing.id)
                        .values(
                            config=dumps_config(next_config),
                            enable=ro.enable if ro.enable is not None else existing.enable,
                            last_modified_time=now,
                        )
                        .returning(Integration.__table__)
                    )
                )
                .mappings()
                .first()
            )
            await session.commit()
            return self._integration_row_vo(updated)

    async def update_integration(
        self, integration_id: str, ro: Any, space_id: str
    ) -> dict[str, Any]:
        from .ai_integration import dumps_config, normalize_space_ai_integration_config

        values: dict[str, Any] = {}
        if ro.enable is not None:
            values["enable"] = ro.enable
        if ro.config is not None:
            values["config"] = dumps_config(normalize_space_ai_integration_config(ro.config))
        values["last_modified_time"] = datetime.now(UTC).replace(tzinfo=None)
        async with db_engine.session() as session:
            updated = (
                (
                    await session.execute(
                        Integration.__table__.update()
                        .where(Integration.id == integration_id)
                        .values(**values)
                        .returning(Integration.__table__)
                    )
                )
                .mappings()
                .first()
            )
            if updated is None:
                raise ApiError("Internal Server Error", HttpErrorCode.INTERNAL_SERVER_ERROR)
            await session.commit()
            return self._integration_row_vo(updated)

    async def delete_integration(self, integration_id: str, space_id: str) -> None:
        async with db_engine.session() as session:
            result = await session.execute(
                sa_delete(Integration).where(Integration.id == integration_id)
            )
            await session.commit()
        if result.rowcount == 0:
            raise ApiError("Internal Server Error", HttpErrorCode.INTERNAL_SERVER_ERROR)

    async def test_integration_llm(self, ro: Any) -> dict[str, Any]:
        from ..setting.service import SettingService

        return await SettingService().test_llm(ro)

    async def get_space_by_id(self, space_id: str) -> dict[str, Any]:
        space = await repository.get_space_row(space_id)
        if space is None:
            raise _not_found()
        role = await self._role_by_space_id(space_id)
        if role is None:
            raise _no_permission()
        return {
            "id": space["id"],
            "name": space["name"],
            "avatar": get_public_full_storage_url(space["avatar"]) if space["avatar"] else None,
            "role": role,
        }

    @staticmethod
    async def _role_by_space_id(space_id: str) -> str | None:
        from ...core.security.permissions import get_max_level_role

        user_id = cls.get("user.id")
        rows = await repository.list_collaborators_by_principals_and_resources(
            [user_id], [space_id]
        )
        if not rows:
            return None
        return str(get_max_level_role(rows))

    async def get_space_list(self) -> list[dict[str, Any]]:
        from ...core.security.permissions import get_max_level_role

        user_id = cls.get("user.id")
        rows = await repository.list_collaborator_rows_by_principals([user_id])
        space_ids = [r["resource_id"] for r in rows if r["resource_type"] == RESOURCE_SPACE]
        if not space_ids:
            return []
        spaces = await repository.list_space_rows_by_ids(space_ids)
        space_rows = [
            r
            for r in rows
            if r["resource_type"] == RESOURCE_SPACE and r["resource_id"] in space_ids
        ]
        role_map: dict[str, str] = {}
        for row in space_rows:
            current = role_map.get(row["resource_id"])
            if current is None:
                role_map[row["resource_id"]] = row["role_name"]
            else:
                best = get_max_level_role([{"role_name": current}, {"role_name": row["role_name"]}])
                role_map[row["resource_id"]] = str(best)
        return [
            {
                "id": space["id"],
                "name": space["name"],
                "avatar": get_public_full_storage_url(space["avatar"]) if space["avatar"] else None,
                "role": role_map[space["id"]],
            }
            for space in spaces
        ]

    # -- update / delete ----------------------------------------------------------

    async def update_space(self, space_id: str, name: str | None) -> dict[str, Any]:
        fields: dict[str, Any] = {"last_modified_by": cls.get("user.id")}
        if name is not None:
            fields["name"] = name
        row = await repository.update_space_row(space_id, fields)
        if row is None:
            # prisma update on a missing/deleted row throws P2025 -> 500
            raise RuntimeError("Record to update not found")
        return {"id": row["id"], "name": row["name"]}

    async def update_space_avatar(self, space_id: str, file_bytes: bytes | None) -> None:
        if file_bytes is None:
            # multer leaves the field undefined; upstream dereferences it -> 500
            raise RuntimeError("Cannot read properties of undefined (reading 'path')")
        space = await repository.get_space_row(space_id)
        if space is None:
            raise _not_found()
        from ...core.avatar import AVATAR_OUTPUT_MIMETYPE, crop_square_avatar

        cropped = crop_square_avatar(file_bytes)
        path = f"{SPACE_AVATAR_DIR}/{space_id}"
        storage = get_storage()
        result = storage.upload_file("public", path, cropped)
        await upsert_attachment_by_token(
            {
                "token": space_id,
                "hash": result["hash"],
                "size": len(cropped),
                "mimetype": AVATAR_OUTPUT_MIMETYPE,
                "path": path,
                "created_by": cls.get("user.id"),
            }
        )
        await repository.update_space_row(
            space_id,
            {
                "avatar": f"{path}?v={int(time.time() * 1000)}",
                "last_modified_by": cls.get("user.id"),
            },
        )

    async def delete_space(self, space_id: str) -> None:
        if not await repository.soft_delete_space(space_id, cls.get("user.id")):
            raise _not_found()
        from ..trash.listener import record_resource_deleted

        space = await repository.get_space_row(space_id, include_deleted=True)
        if space is not None:
            await record_resource_deleted(
                "space", space_id, None, space["deleted_time"]
            )

    async def permanent_delete_space(self, space_id: str) -> dict[str, Any]:
        from ...core.security.permissions import PermissionService

        await PermissionService().valid_permissions(
            space_id, ["space|delete"], cls.get("accessTokenId"), True
        )
        space = await repository.get_space_row(space_id, include_deleted=True)
        if space is None:
            raise _not_found()
        bases = await repository.list_base_rows_by_space(space_id)
        for base in bases:
            await repository.delete_base_row(base["id"])
        await self._clean_space_related_data(space_id)
        return {"spaceId": space_id, "permanent": True}

    @staticmethod
    async def _clean_space_related_data(space_id: str) -> None:
        async with db_engine.session() as session:
            await session.execute(
                sa_delete(Collaborator).where(
                    Collaborator.resource_id == space_id,
                    Collaborator.resource_type == RESOURCE_SPACE,
                )
            )
            await session.execute(
                sa_delete(InvitationRecord).where(InvitationRecord.space_id == space_id)
            )
            await session.execute(sa_delete(Integration).where(Integration.resource_id == space_id))
            await session.execute(
                sa_delete(Trash).where(
                    Trash.resource_type == "space", Trash.resource_id == space_id
                )
            )
            await session.commit()
        await repository.delete_invitation_rows(space_id)
        await repository.delete_space_row(space_id)

    # -- base listing --------------------------------------------------------------

    async def get_base_list_by_space_id(self, space_id: str) -> list[dict[str, Any]]:
        view = await self.collaborators.get_current_user_collaborators_base_and_space_array()
        if space_id not in view["spaceIds"]:
            raise _no_permission()
        bases = await repository.list_base_rows_by_space(space_id)
        if not bases:
            return []
        user_ids = list({b["created_by"] for b in bases})
        users = await repository.list_user_rows_by_ids(user_ids)
        user_map = {u["id"]: u for u in users}
        base_ids = [b["id"] for b in bases]
        async with db_engine.session() as session:
            from sqlalchemy import select

            shared_rows = (
                await session.execute(
                    select(BaseShare.base_id).where(
                        BaseShare.base_id.in_(base_ids),
                        BaseShare.node_id.is_(None),
                        BaseShare.enabled.is_(True),
                    )
                )
            ).all()
        shared_ids = {r[0] for r in shared_rows}
        result = []
        for base in bases:
            created_user = user_map.get(base["created_by"])
            item: dict[str, Any] = {
                "id": base["id"],
                "name": base["name"],
                "order": base["order"],
                "spaceId": base["space_id"],
                "icon": base["icon"],
                "createdBy": base["created_by"],
                "lastModifiedTime": _iso(base["last_modified_time"]),
                "createdTime": _iso(base["created_time"]),
                "role": view["roleMap"].get(base["id"]) or view["roleMap"].get(space_id),
                "isShared": base["id"] in shared_ids,
            }
            if base.get("v2_enabled"):
                item["v2Status"] = {"useV2": True, "reason": "new_base"}
            if created_user:
                item["createdUser"] = {
                    "id": created_user["id"],
                    "name": created_user["name"],
                    "avatar": get_public_full_storage_url(created_user["avatar"])
                    if created_user["avatar"]
                    else None,
                }
            result.append(item)
        return result

    async def get_base_entry_map(self, space_id: str, take: int | None) -> dict[str, str]:
        base_list = await self.get_base_list_by_space_id(space_id)
        capped = base_list[:take] if take else base_list
        user_id = cls.get("user.id")
        return await self._base_entry_map(user_id, [b["id"] for b in capped])

    @staticmethod
    async def _base_entry_map(user_id: str, base_ids: list[str]) -> dict[str, str]:
        if not base_ids:
            return {}
        async with db_engine.session() as session:
            from sqlalchemy import select

            rows = (
                (
                    await session.execute(
                        select(UserLastVisit)
                        .where(
                            UserLastVisit.user_id == user_id,
                            UserLastVisit.parent_resource_id.in_(base_ids),
                            UserLastVisit.resource_type.in_(
                                ["table", "dashboard", "workflow", "app"]
                            ),
                        )
                        .order_by(UserLastVisit.last_visit_time.desc())
                    )
                )
                .scalars()
                .all()
            )
        latest: dict[str, Any] = {}
        for row in rows:
            if row.parent_resource_id not in latest:
                latest[row.parent_resource_id] = row
        table_id_to_base: dict[str, str] = {}
        for base_id, node in latest.items():
            if node.resource_type == "table":
                table_id_to_base[node.resource_id] = base_id
        if not table_id_to_base:
            return {}
        # resolveTableEntryUrls: last visited view when alive, else first by order
        async with db_engine.session() as session:
            from sqlalchemy import select

            visits = (
                (
                    await session.execute(
                        select(UserLastVisit)
                        .where(
                            UserLastVisit.user_id == user_id,
                            UserLastVisit.resource_type == "view",
                            UserLastVisit.parent_resource_id.in_(table_id_to_base.keys()),
                        )
                        .order_by(UserLastVisit.last_visit_time.desc())
                    )
                )
                .scalars()
                .all()
            )
        view_by_table: dict[str, Any] = {}
        for visit in visits:
            if visit.parent_resource_id not in view_by_table:
                view_by_table[visit.parent_resource_id] = visit.resource_id
        entry_map: dict[str, str] = {}
        for table_id, base_id in table_id_to_base.items():
            view_id = view_by_table.get(table_id)
            if view_id:
                entry_map[base_id] = f"/base/{base_id}/table/{table_id}/{view_id}"
        return entry_map

    # -- search ----------------------------------------------------------------------

    async def search(self, space_id: str, query: dict[str, Any]) -> dict[str, Any]:
        search: str = query["search"]
        page_size: int = query.get("pageSize") or 10
        cursor: str | None = query.get("cursor")
        filter_type: str | None = (query.get("type") or "").lower() or None

        bases = await repository.list_base_rows_by_space(space_id)
        if not bases:
            return {"list": [], "total": 0, "nextCursor": None}
        base_ids = [b["id"] for b in bases]

        mapping = {
            "base": ("base", True, True),
            "table": ("table_meta", True, True),
            "dashboard": ("dashboard", False, False),
        }
        types = [filter_type] if filter_type else list(mapping)
        cursor_data = self._parse_cursor(cursor)

        parts = []
        for resource_type in types:
            if resource_type not in mapping:
                continue
            table, has_deleted, has_icon = mapping[resource_type]
            icon_expr = "icon" if has_icon else "NULL"
            base_id_expr = "id" if resource_type == "base" else "base_id"
            deleted_filter = "AND deleted_time IS NULL" if has_deleted else ""
            id_filter = (
                "AND id = ANY(:ids)" if resource_type == "base" else "AND base_id = ANY(:ids)"
            )
            select_part = (
                f"SELECT id, name, :t_{resource_type} AS type, "
                f"COALESCE({icon_expr}, NULL) AS icon, "
                f"{base_id_expr} AS base_id, created_by, created_time FROM {table} "
            )
            parts.append(
                f"{select_part}WHERE name ILIKE :search {deleted_filter} {id_filter}"
            )
        if not parts:
            return {"list": [], "total": 0, "nextCursor": None}
        union_sql = " UNION ALL ".join(parts)
        params: dict[str, Any] = {
            "search": f"%{search}%",
            "ids": base_ids,
            "t_base": "base",
            "t_table": "table",
            "t_dashboard": "dashboard",
        }
        total_expr = "COUNT(*) OVER() AS total_count" if not cursor_data else "0 AS total_count"
        where_cursor = ""
        if cursor_data:
            where_cursor = "AND (created_time, id) < (:c_time, :c_id)"
            params["c_time"] = cursor_data["time"]
            params["c_id"] = cursor_data["id"]
        sql = (
            f"SELECT *, {total_expr} FROM ({union_sql}) AS combined "
            f"WHERE 1=1 {where_cursor} "
            "ORDER BY created_time DESC, id DESC LIMIT :limit"
        )
        params["limit"] = page_size + 1
        async with db_engine.session() as session:
            from sqlalchemy import text

            rows = (await session.execute(text(sql), params)).mappings().all()
        total = int(rows[0]["total_count"]) if rows and not cursor_data else 0
        has_more = len(rows) > page_size
        results = list(rows[:page_size]) if has_more else list(rows)

        base_map = {b["id"]: b for b in bases}
        user_ids = {r["created_by"] for r in results if r["created_by"]}
        space_ids_for_bases = {
            base_map[r["base_id"]]["space_id"] for r in results if r["type"] == "base"
        }
        owner_map = await self._space_owner_map(list(space_ids_for_bases))
        user_ids = user_ids | set(owner_map.values())
        users = await repository.list_user_rows_by_ids(list(user_ids))
        user_map = {u["id"]: u for u in users}

        result_list = []
        for row in results:
            base = base_map.get(row["base_id"])
            created_by = row["created_by"]
            if row["type"] == "base":
                display_id = (
                    created_by
                    if created_by in user_map
                    else owner_map.get((base or {}).get("space_id", ""))
                )
            else:
                display_id = created_by
            display_user = user_map.get(display_id) if display_id else None
            item: dict[str, Any] = {
                "id": row["id"],
                "name": row["name"],
                "type": row["type"],
                "icon": row["icon"],
                "baseId": row["base_id"],
                "baseName": (base or {}).get("name", ""),
                "createdTime": _iso(row["created_time"]),
            }
            if display_user:
                item["createdUser"] = {
                    "id": display_user["id"],
                    "name": display_user["name"],
                    "avatar": get_public_full_storage_url(display_user["avatar"])
                    if display_user["avatar"]
                    else None,
                }
            result_list.append(item)
        next_cursor = None
        if has_more and results:
            last = results[-1]
            next_cursor = f"{_iso(last['created_time'])}_{last['id']}"
        return {"list": result_list, "total": total, "nextCursor": next_cursor}

    @staticmethod
    def _parse_cursor(cursor: str | None) -> dict[str, str] | None:
        if not cursor:
            return None
        index = cursor.rfind("_")
        if index == -1:
            return None
        return {"time": cursor[:index], "id": cursor[index + 1 :]}

    async def _space_owner_map(self, space_ids: list[str]) -> dict[str, str]:
        """spaceId -> one owner user id (buildSpaceOwnerContext, first match)."""
        if not space_ids:
            return {}
        rows = await repository.list_collaborator_rows(space_ids, resource_type=RESOURCE_SPACE)
        owner_ids = [r["principal_id"] for r in rows if r["role_name"] == "owner"]
        owners = await repository.list_user_rows_by_ids(owner_ids)
        active = {u["id"] for u in owners if u["deleted_time"] is None}
        result: dict[str, str] = {}
        for row in rows:
            if (
                row["resource_id"] not in result
                and row["role_name"] == "owner"
                and row["principal_id"] in active
            ):
                result[row["resource_id"]] = row["principal_id"]
        return result
