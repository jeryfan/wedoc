"""base domain service — ports features/base/base.service.ts.

Link invitations, collaborator management and the shared-base listing live in
their own services; this service owns the base row lifecycle plus the
permission/role views. Import/duplicate/template/export/connection/move/erd/
publish routes are not registered yet (see docs/api-parity-ledger.md).
"""

from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import delete, select, text

from ...core import cls
from ...core.errors import ApiError, HttpErrorCode
from ...core.ids import IdPrefix, new_id, random_string
from ...core.security.permissions import get_max_level_role
from ...core.storage import get_public_full_storage_url
from ...db import engine as db_engine
from ...db.models_meta import BaseShare, Collaborator, Invitation, InvitationRecord, Trash
from ...db.provider import create_schema_sql, drop_schema_sql
from ..collaborator.service import RESOURCE_BASE, RESOURCE_SPACE, CollaboratorService
from ..space import repository as space_repository
from . import repository

logger = structlog.get_logger(__name__)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


# getPermission() key set: Table + Base + Automation + App + TableRecordHistory
# prefixes (packages/core action order).
_PERMISSION_ACTIONS: tuple[str, ...] = (
    "table|create",
    "table|delete",
    "table|read",
    "table|update",
    "table|import",
    "table|export",
    "table|trash_read",
    "table|trash_update",
    "table|trash_reset",
    "table|archive_read",
    "table|archive_manage",
    "base|create",
    "base|delete",
    "base|read",
    "base|read_all",
    "base|update",
    "base|invite_email",
    "base|invite_link",
    "base|table_import",
    "base|table_export",
    "base|authority_matrix_config",
    "base|db_connection",
    "base|query_data",
    "automation|create",
    "automation|delete",
    "automation|read",
    "automation|update",
    "app|create",
    "app|delete",
    "app|read",
    "app|update",
    "table_record_history|read",
)


def _not_found() -> ApiError:
    return ApiError(
        "Base not found",
        HttpErrorCode.NOT_FOUND,
        {"localization": {"i18nKey": "httpErrors.base.notFound"}},
    )


class BaseService:
    def __init__(self) -> None:
        self.collaborators = CollaboratorService()

    async def create_base(
        self, space_id: str, name: str | None, icon: str | None
    ) -> dict[str, Any]:
        user_id = cls.get("user.id")
        space = await space_repository.get_space_row(space_id)
        if space is None:
            raise ApiError(
                "Space not found",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.space.notFound"}},
            )
        order = await repository.get_max_order(space_id) + 1
        base_id = new_id(IdPrefix.BASE)
        now = datetime.now(UTC).replace(tzinfo=None)
        row = await repository.insert_base(
            {
                "id": base_id,
                "name": name or "Untitled Base",
                "space_id": space_id,
                "order": order,
                "icon": icon,
                "v2_enabled": True,
                "created_by": user_id,
                "created_time": now,
                "last_modified_by": user_id,
                "provision_state": "pending",
            }
        )
        try:
            async with db_engine.session() as session:
                for sql in create_schema_sql(base_id):
                    await session.execute(text(sql))
                await session.commit()
            await repository.update_base_row(
                base_id, {"provision_state": "ready", "last_modified_by": user_id}
            )
        except Exception:
            await repository.update_base_row(
                base_id, {"provision_state": "error", "last_modified_by": user_id}
            )
            raise
        await self._mark_base_visited(base_id, space_id)
        return {
            "id": row["id"],
            "name": row["name"],
            "icon": row["icon"],
            "spaceId": row["space_id"],
        }

    async def _mark_base_visited(self, base_id: str, space_id: str) -> None:
        user_id = cls.get("user.id")
        if not user_id:
            return
        try:
            now = datetime.now(UTC).replace(tzinfo=None)
            await repository.mark_base_visited(
                random_string(16), user_id, base_id, space_id, now
            )
        except Exception as exc:  # ref only warns here
            logger.warn("failed to seed last-visit for base", base_id=base_id, error=str(exc))

    async def get_base_by_id(self, base_id: str) -> dict[str, Any]:
        base = await repository.get_base_row(base_id)
        if base is None:
            raise _not_found()
        role, collaborator_type = await self._role_by_base_id(base_id, base["space_id"])
        result: dict[str, Any] = {
            "id": base["id"],
            "name": base["name"],
            "icon": base["icon"],
            "spaceId": base["space_id"],
            "createdBy": base["created_by"],
            "role": role,
            "collaboratorType": collaborator_type,
        }
        if base.get("v2_enabled"):
            result["v2Status"] = {"useV2": True, "reason": "new_base"}
        return result

    async def _role_by_base_id(self, base_id: str, space_id: str) -> tuple[str, str]:
        user_id = cls.get("user.id")
        rows = await space_repository.list_collaborators_by_principals_and_resources(
            [user_id], [base_id, space_id]
        )
        if not rows:
            raise ApiError(
                "Cannot access base",
                HttpErrorCode.RESTRICTED_RESOURCE,
                {
                    "localization": {
                        "i18nKey": "httpErrors.base.cannotAccess",
                        "context": {"baseId": base_id},
                    }
                },
            )
        role = get_max_level_role(rows)
        # On equal roles prefer the space row (space features gate on it).
        collaborator = next(
            (r for r in rows if r["role_name"] == role and r["resource_type"] == RESOURCE_SPACE),
            None,
        ) or next((r for r in rows if r["role_name"] == role), None)
        return role, collaborator["resource_type"] if collaborator else RESOURCE_SPACE

    async def update_base(
        self, base_id: str, name: str | None, icon: str | None, icon_set: bool
    ) -> dict[str, Any]:
        fields: dict[str, Any] = {"last_modified_by": cls.get("user.id")}
        if name is not None:
            fields["name"] = name
        if icon_set:
            fields["icon"] = icon
        row = await repository.update_base_row(base_id, fields)
        if row is None or row["deleted_time"] is not None:
            raise _not_found()
        return {
            "id": row["id"],
            "name": row["name"],
            "spaceId": row["space_id"],
            "icon": row["icon"],
        }

    async def update_order(self, base_id: str, anchor_id: str, position: str) -> None:
        base = await repository.get_base_row(base_id)
        if base is None:
            raise _not_found()
        anchor = await self._get_anchor(base["space_id"], anchor_id)
        new_order = await self._compute_order(base["space_id"], anchor, position)
        await repository.update_base_row(base_id, {"order": new_order})

    async def _get_anchor(self, space_id: str, anchor_id: str) -> dict[str, Any]:
        anchor = await repository.get_base_row(anchor_id)
        if anchor is None or anchor["space_id"] != space_id:
            raise ApiError(
                "Anchor base not found",
                HttpErrorCode.NOT_FOUND,
                {
                    "localization": {
                        "i18nKey": "httpErrors.base.anchorNotFound",
                        "context": {"anchorId": anchor_id},
                    }
                },
            )
        return anchor

    async def _compute_order(
        self, space_id: str, anchor: dict[str, Any], position: str
    ) -> float:
        below = position == "after"
        neighbor = await repository.list_next_base_by_order(
            space_id, anchor["order"], below=position == "before"
        )
        if neighbor is None:
            return anchor["order"] + (1 if below else -1)
        order = (neighbor["order"] + anchor["order"]) / 2
        if abs(order - anchor["order"]) < 2 * 2.220446049250313e-16:
            # gap exhausted: re-shuffle the space to integral orders, recompute.
            await self._shuffle_orders(space_id)
            anchor = await self._get_anchor(space_id, anchor["id"])
            return await self._compute_order(space_id, anchor, position)
        return order

    async def _shuffle_orders(self, space_id: str) -> None:
        bases = await space_repository.list_base_rows_by_space(space_id)
        for index, base in enumerate(bases, start=1):
            await repository.update_base_row(base["id"], {"order": float(index)})

    async def delete_base(self, base_id: str) -> None:
        base = await repository.get_base_row(base_id)
        if base is None:
            raise _not_found()
        await repository.soft_delete_base_row(
            base_id, datetime.now(UTC).replace(tzinfo=None)
        )

    async def permanent_delete_base(self, base_id: str) -> None:
        base = await repository.get_base_row(base_id, include_deleted=True)
        if base is None:
            raise _not_found()
        async with db_engine.session() as session:
            await session.execute(text(drop_schema_sql(base_id)))
            await session.commit()
        await self._clean_base_related_data(base_id)

    async def _clean_base_related_data(self, base_id: str) -> None:
        async with db_engine.session() as session:
            await session.execute(
                delete(Collaborator).where(
                    Collaborator.resource_id == base_id,
                    Collaborator.resource_type == RESOURCE_BASE,
                )
            )
            await session.execute(delete(Invitation).where(Invitation.base_id == base_id))
            await session.execute(
                delete(InvitationRecord).where(InvitationRecord.base_id == base_id)
            )
            await session.execute(delete(Trash).where(Trash.resource_id == base_id))
            await session.commit()
        await space_repository.delete_base_row(base_id)

    async def get_all_base_list(self) -> list[dict[str, Any]]:
        view = await self.collaborators.get_current_user_collaborators_base_and_space_array()
        bases = await repository.list_base_rows_by_space_any(
            view["spaceIds"], view["baseIds"]
        )
        if not bases:
            return []
        base_ids = [b["id"] for b in bases]
        shared_ids = await self._shared_base_ids(base_ids)
        users = await space_repository.list_user_rows_by_ids(
            list({b["created_by"] for b in bases})
        )
        user_map = {u["id"]: u for u in users}
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
                "role": view["roleMap"].get(base["id"]) or view["roleMap"].get(base["space_id"]),
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

    @staticmethod
    async def _shared_base_ids(base_ids: list[str]) -> set[str]:
        async with db_engine.session() as session:
            rows = (
                (
                    await session.execute(
                        select(BaseShare.base_id).where(
                            BaseShare.base_id.in_(base_ids),
                            BaseShare.node_id.is_(None),
                            BaseShare.enabled.is_(True),
                        )
                    )
                )
                .all()
            )
        return {r[0] for r in rows}

    async def get_permission(self) -> dict[str, bool]:
        granted = cls.get("permissions") or []
        return {action: action in granted for action in _PERMISSION_ACTIONS}
