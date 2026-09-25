"""Permission layer: role -> permission-set matrix (transcribed 1:1 from the
upstream packages/core auth definitions), PermissionService (collaborator role
resolution + token/share/template checks), and the PermissionGuard equivalent.
"""

import json
import re
from enum import StrEnum
from typing import Any

from fastapi import Request
from sqlalchemy import column, select, table

from ...db import engine as db_engine
from .. import cls
from ..errors import ApiError, HttpErrorCode
from .auth import (
    AllowAnonymousType,
    JwtService,
    _get_meta,
    route_endpoint,
)
from .constants import ANONYMOUS_USER_ID, IdPrefix, is_anonymous

# ---------------------------------------------------------------------------
# actions (packages/core/src/auth/actions.ts)
# ---------------------------------------------------------------------------

Action = str

SPACE_ACTIONS: tuple[Action, ...] = (
    "space|create",
    "space|delete",
    "space|read",
    "space|update",
    "space|invite_email",
    "space|invite_link",
    "space|grant_role",
)
BASE_ACTIONS: tuple[Action, ...] = (
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
)
TABLE_ACTIONS: tuple[Action, ...] = (
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
)
VIEW_ACTIONS: tuple[Action, ...] = (
    "view|create",
    "view|delete",
    "view|read",
    "view|update",
    "view|share",
)
FIELD_ACTIONS: tuple[Action, ...] = (
    "field|create",
    "field|delete",
    "field|read",
    "field|update",
)
RECORD_ACTIONS: tuple[Action, ...] = (
    "record|create",
    "record|delete",
    "record|read",
    "record|update",
    "record|comment",
    "record|copy",
    "record|archive",
)
AUTOMATION_ACTIONS: tuple[Action, ...] = (
    "automation|create",
    "automation|delete",
    "automation|read",
    "automation|update",
)
APP_ACTIONS: tuple[Action, ...] = ("app|create", "app|delete", "app|read", "app|update")
USER_ACTIONS: tuple[Action, ...] = ("user|email_read", "user|integrations")
TABLE_RECORD_HISTORY_ACTIONS: tuple[Action, ...] = ("table_record_history|read",)
INSTANCE_ACTIONS: tuple[Action, ...] = ("instance|read", "instance|update")
ENTERPRISE_ACTIONS: tuple[Action, ...] = ("enterprise|read", "enterprise|update")

ALL_ACTIONS: tuple[Action, ...] = (
    *SPACE_ACTIONS,
    *BASE_ACTIONS,
    *TABLE_ACTIONS,
    *VIEW_ACTIONS,
    *FIELD_ACTIONS,
    *RECORD_ACTIONS,
    *TABLE_RECORD_HISTORY_ACTIONS,
    *AUTOMATION_ACTIONS,
    *APP_ACTIONS,
    *USER_ACTIONS,
    *INSTANCE_ACTIONS,
    *ENTERPRISE_ACTIONS,
)

# ---------------------------------------------------------------------------
# roles (packages/core/src/auth/role)
# ---------------------------------------------------------------------------


class Role(StrEnum):
    OWNER = "owner"
    CREATOR = "creator"
    EDITOR = "editor"
    COMMENTER = "commenter"
    VIEWER = "viewer"


ROLE_LEVEL = [Role.OWNER, Role.CREATOR, Role.EDITOR, Role.COMMENTER, Role.VIEWER]
BILLABLE_ROLES = (Role.OWNER, Role.CREATOR, Role.EDITOR)

# RolePermission maps transcribed from role/constant.ts; only granted (true)
# entries are kept, which is exactly what getPermissions() filters for.
_OWNER_GRANTS: frozenset[Action] = frozenset(
    {
        "space|create",
        "space|delete",
        "space|read",
        "space|update",
        "space|invite_email",
        "space|invite_link",
        "space|grant_role",
        "base|create",
        "base|delete",
        "base|read",
        "base|update",
        "base|invite_email",
        "base|invite_link",
        "base|table_import",
        "base|table_export",
        "base|authority_matrix_config",
        "base|db_connection",
        "base|query_data",
        "base|read_all",
        "table|create",
        "table|read",
        "table|delete",
        "table|update",
        "table|import",
        "table|export",
        "table|trash_read",
        "table|trash_update",
        "table|trash_reset",
        "table|archive_read",
        "table|archive_manage",
        "table_record_history|read",
        "view|create",
        "view|delete",
        "view|read",
        "view|update",
        "view|share",
        "field|create",
        "field|delete",
        "field|read",
        "field|update",
        "record|create",
        "record|comment",
        "record|delete",
        "record|archive",
        "record|read",
        "record|update",
        "record|copy",
        "automation|create",
        "automation|delete",
        "automation|read",
        "automation|update",
        "app|create",
        "app|delete",
        "app|read",
        "app|update",
        "user|email_read",
        "user|integrations",
    }
)

_CREATOR_GRANTS: frozenset[Action] = frozenset(
    {
        "space|read",
        "space|invite_email",
        "space|invite_link",
        "base|create",
        "base|delete",
        "base|read",
        "base|update",
        "base|read_all",
        "base|invite_email",
        "base|invite_link",
        "base|table_import",
        "base|table_export",
        "base|authority_matrix_config",
        "base|query_data",
        "table|create",
        "table|read",
        "table|delete",
        "table|update",
        "table|import",
        "table|export",
        "table|trash_read",
        "table|trash_update",
        "table|trash_reset",
        "table|archive_read",
        "table|archive_manage",
        "table_record_history|read",
        "view|create",
        "view|delete",
        "view|read",
        "view|update",
        "view|share",
        "field|create",
        "field|delete",
        "field|read",
        "field|update",
        "record|create",
        "record|comment",
        "record|delete",
        "record|archive",
        "record|read",
        "record|update",
        "record|copy",
        "automation|create",
        "automation|delete",
        "automation|read",
        "automation|update",
        "app|create",
        "app|delete",
        "app|read",
        "app|update",
        "user|email_read",
        "user|integrations",
    }
)

_EDITOR_GRANTS: frozenset[Action] = frozenset(
    {
        "space|read",
        "space|invite_email",
        "base|read",
        "base|read_all",
        "base|invite_email",
        "base|table_import",
        "base|table_export",
        "base|query_data",
        "table|read",
        "table|export",
        "table|trash_read",
        "table|trash_update",
        "table|archive_read",
        "table_record_history|read",
        "view|create",
        "view|delete",
        "view|read",
        "view|update",
        "view|share",
        "field|read",
        "record|create",
        "record|comment",
        "record|delete",
        "record|archive",
        "record|read",
        "record|update",
        "record|copy",
        "automation|read",
        "app|read",
        "user|email_read",
        "user|integrations",
    }
)

_COMMENTER_GRANTS: frozenset[Action] = frozenset(
    {
        "space|read",
        "space|invite_email",
        "base|read",
        "base|read_all",
        "base|invite_email",
        "base|table_export",
        "table|read",
        "table|export",
        "view|read",
        "field|read",
        "record|comment",
        "record|read",
        "record|copy",
        "automation|read",
        "app|read",
        "user|email_read",
        "user|integrations",
    }
)

_VIEWER_GRANTS: frozenset[Action] = frozenset(
    {
        "space|read",
        "space|invite_email",
        "base|read",
        "base|read_all",
        "base|invite_email",
        "base|table_export",
        "base|query_data",
        "table|read",
        "table|export",
        "view|read",
        "field|read",
        "record|read",
        "record|copy",
        "automation|read",
        "app|read",
        "user|email_read",
        "user|integrations",
    }
)

ROLE_PERMISSIONS: dict[Role, frozenset[Action]] = {
    Role.OWNER: _OWNER_GRANTS,
    Role.CREATOR: _CREATOR_GRANTS,
    Role.EDITOR: _EDITOR_GRANTS,
    Role.COMMENTER: _COMMENTER_GRANTS,
    Role.VIEWER: _VIEWER_GRANTS,
}

# role/template.ts: pure read access + base|query_data
TEMPLATE_PERMISSIONS: list[Action] = [
    "base|read",
    "table|read",
    "view|read",
    "field|read",
    "record|read",
    "automation|read",
    "app|read",
    "base|query_data",
]

# role/share.ts
SHARE_VIEW_READ_ONLY_PERMISSIONS: list[Action] = list(TEMPLATE_PERMISSIONS)
SHARE_VIEW_EDIT_PERMISSIONS: list[Action] = [
    *TEMPLATE_PERMISSIONS,
    "record|create",
    "record|update",
    "record|delete",
]

# Permissions that must never be granted via share links (permission.service.ts)
SHARE_EXCLUDED_PERMISSIONS: frozenset[Action] = frozenset(
    {
        "view|share",
        "space|invite_email",
        "base|invite_email",
        "user|email_read",
        "user|integrations",
    }
)

SHARE_VIEW_COMMON_WRITE_PERMISSIONS: frozenset[Action] = frozenset(
    {"record|create", "record|update", "record|delete"}
)

# Endpoint rules for the share-view header (permission.guard.ts)
SHARE_VIEW_ENDPOINT_RULES: list[tuple[re.Pattern[str], frozenset[Action]]] = [
    (
        re.compile(r"^/api/table/[^/]+/(?:record|selection)(?:/|$)"),
        SHARE_VIEW_COMMON_WRITE_PERMISSIONS,
    ),
    (
        re.compile(r"^/api/table/[^/]+/undo-redo(?:/|$)"),
        frozenset({"table|read"}),
    ),
]

SHARE_VIEW_EDITABLE_TYPES = {"grid", "kanban", "gallery", "calendar"}

TEMPLATE_HEADER = "X-Tea-Template"
BASE_SHARE_ID_HEADER = "X-Tea-Base-Share"
SHARE_VIEW_ID_HEADER = "X-Tea-Share-View"


def get_permission_map(role: str | Role) -> frozenset[Action]:
    return ROLE_PERMISSIONS[Role(role)]


def get_permissions(role: str | Role) -> list[Action]:
    grants = get_permission_map(role)
    # Preserve upstream key order (insertion order of the RolePermission map)
    return [action for action in ALL_ACTIONS if action in grants]


def check_permissions(role: str | Role, actions: list[Action]) -> bool:
    grants = get_permission_map(role)
    return all(action in grants for action in actions)


def has_permission(role: str | Role, action: Action) -> bool:
    return check_permissions(role, [action])


def is_restricted_role(role: str | Role) -> bool:
    return Role(role) != Role.OWNER


def can_manage_role(manager_role: str, target_role: str) -> bool:
    return ROLE_LEVEL.index(Role(manager_role)) < ROLE_LEVEL.index(Role(target_role))


def get_max_level_role(collaborators: list[dict[str, Any]]) -> Role | None:
    if not collaborators:
        return None
    best = min(collaborators, key=lambda c: ROLE_LEVEL.index(Role(c["role_name"])))
    return Role(best["role_name"])


# ---------------------------------------------------------------------------
# permission service (permission.service.ts port; light table() reflection)
# ---------------------------------------------------------------------------

_collaborator = table(
    "collaborator",
    column("id"),
    column("role_name"),
    column("resource_type"),
    column("resource_id"),
    column("principal_id"),
    column("principal_type"),
)
_space = table("space", column("id"), column("deleted_time"))
_base = table("base", column("id"), column("space_id"), column("deleted_time"))
_table_meta = table(
    "table_meta", column("id"), column("base_id"), column("deleted_time")
)
_view = table(
    "view",
    column("id"),
    column("table_id"),
    column("type"),
    column("share_id"),
    column("share_meta"),
    column("enable_share"),
    column("deleted_time"),
)
_field = table("field", column("id"), column("table_id"), column("type"),
               column("options"), column("deleted_time"))
_base_share = table(
    "base_share",
    column("id"),
    column("base_id"),
    column("share_id"),
    column("password"),
    column("node_id"),
    column("allow_copy"),
    column("allow_edit"),
    column("enabled"),
)
_base_node = table(
    "base_node",
    column("id"),
    column("parent_id"),
    column("base_id"),
    column("resource_type"),
    column("resource_id"),
)
_template = table("template", column("id"), column("base_id"), column("snapshot"))
_access_token = table(
    "access_token",
    column("id"),
    column("user_id"),
    column("scopes"),
    column("space_ids"),
    column("base_ids"),
    column("client_id"),
    column("has_full_access"),
)


class PermissionService:
    def __init__(self, jwt_service: JwtService | None = None) -> None:
        self._jwt = jwt_service or JwtService()

    # -- collaborator / role resolution --------------------------------------
    @staticmethod
    def _department_ids() -> list[str]:
        departments = cls.get("organization.departments") or []
        return [department["id"] for department in departments]

    async def _collaborators_by_resource(self, resource_id: str) -> list[dict[str, Any]]:
        async with db_engine.session() as session:
            rows = (
                (
                    await session.execute(
                        select(_collaborator).where(_collaborator.c.resource_id == resource_id)
                    )
                )
                .mappings()
                .all()
            )
        return [dict(row) for row in rows]

    async def get_role_by_space_id(
        self, space_id: str, include_inactive_resource: bool = False
    ) -> Role | None:
        user_id = cls.get("user.id")
        principals = [*self._department_ids(), user_id]
        collaborators = await self._collaborators_by_resource(space_id)
        collaborators = [c for c in collaborators if c["principal_id"] in principals]
        async with db_engine.session() as session:
            space = (
                await session.execute(select(_space).where(_space.c.id == space_id))
            ).mappings().first()
        if not space:
            raise ApiError(
                f"space {space_id} is not found",
                HttpErrorCode.RESTRICTED_RESOURCE,
                {"localization": {"i18nKey": "httpErrors.space.notFound"}},
            )
        if space["deleted_time"] and not include_inactive_resource:
            raise ApiError(
                f"space {space_id} is deleted",
                HttpErrorCode.RESTRICTED_RESOURCE,
                {"localization": {"i18nKey": "httpErrors.space.deleted"}},
            )
        if not collaborators:
            return None
        return get_max_level_role(collaborators)

    async def get_role_by_base_id(self, base_id: str) -> Role | None:
        user_id = cls.get("user.id")
        principals = [*self._department_ids(), user_id]
        collaborators = await self._collaborators_by_resource(base_id)
        collaborators = [c for c in collaborators if c["principal_id"] in principals]
        if not collaborators:
            return None
        return get_max_level_role(collaborators)

    # -- ancestry --------------------------------------------------------------
    async def get_upper_id_by_base_id(
        self, base_id: str, include_inactive_resource: bool = False
    ) -> str:
        async with db_engine.session() as session:
            base = (
                await session.execute(select(_base).where(_base.c.id == base_id))
            ).mappings().first()
        if not base or (base["deleted_time"] and not include_inactive_resource):
            raise ApiError(
                "Base not found",
                HttpErrorCode.NOT_FOUND,
                {"localization": {"i18nKey": "httpErrors.base.notFound"}},
            )
        space_id = base["space_id"]
        cls.set("spaceId", space_id)
        return space_id

    async def get_upper_id_by_table_id(
        self, table_id: str, include_inactive_resource: bool = False
    ) -> tuple[str, str]:
        async with db_engine.session() as session:
            row = (
                await session.execute(
                    select(_table_meta.c.base_id, _base.c.space_id)
                    .select_from(
                        _table_meta.join(_base, _base.c.id == _table_meta.c.base_id)
                    )
                    .where(_table_meta.c.id == table_id)
                )
            ).first()
        base_id, space_id = (row[0], row[1]) if row else (None, None)
        # Upstream filters soft-deleted tables unless includeInactiveResource
        if row is not None and not include_inactive_resource:
            async with db_engine.session() as session:
                table_row = (
                    await session.execute(
                        select(_table_meta.c.deleted_time).where(
                            _table_meta.c.id == table_id
                        )
                    )
                ).first()
            if table_row and table_row[0]:
                base_id = space_id = None
        if not space_id or not base_id:
            raise ApiError(
                f"Invalid tableId: {table_id}",
                HttpErrorCode.NOT_FOUND,
                {"localization": {"i18nKey": "httpErrors.table.notFound"}},
            )
        cls.set("spaceId", space_id)
        return base_id, space_id

    # -- access token ------------------------------------------------------------
    async def get_access_token(self, access_token_id: str) -> dict[str, Any]:
        async with db_engine.session() as session:
            row = (
                await session.execute(
                    select(_access_token).where(_access_token.c.id == access_token_id)
                )
            ).mappings().first()
        if row is None:
            raise ApiError("token not found", HttpErrorCode.UNAUTHORIZED)
        scopes = json.loads(row["scopes"])
        client_id = row["client_id"]
        if client_id and client_id.startswith(IdPrefix.OAUTH_CLIENT):
            # OAuth app tokens derive resource range from the user's collaborator rows
            space_ids, base_ids = await self._get_oauth_access_by(row["user_id"])
            return {"scopes": scopes, "spaceIds": space_ids, "baseIds": base_ids}
        return {
            "scopes": scopes,
            "spaceIds": json.loads(row["space_ids"]) if row["space_ids"] else None,
            "baseIds": json.loads(row["base_ids"]) if row["base_ids"] else None,
            "hasFullAccess": row["has_full_access"] or None,
        }

    async def _get_oauth_access_by(self, user_id: str) -> tuple[list[str], list[str]]:
        principals = [*self._department_ids(), user_id]
        async with db_engine.session() as session:
            rows = (
                (
                    await session.execute(
                        select(
                            _collaborator.c.role_name,
                            _collaborator.c.resource_id,
                            _collaborator.c.resource_type,
                        ).where(_collaborator.c.principal_id.in_(principals))
                    )
                )
                .mappings()
                .all()
            )
        space_ids = [r["resource_id"] for r in rows if r["resource_type"] == "space"]
        base_ids = [r["resource_id"] for r in rows if r["resource_type"] == "base"]
        return space_ids, base_ids

    async def get_permissions_by_access_token(
        self,
        resource_id: str,
        access_token_id: str,
        include_inactive_resource: bool = False,
    ) -> list[Action]:
        token = await self.get_access_token(access_token_id)
        scopes: list[Action] = token["scopes"]
        space_ids = token.get("spaceIds")
        base_ids = token.get("baseIds")
        if token.get("hasFullAccess"):
            return scopes
        if not resource_id.startswith(
            (IdPrefix.SPACE, IdPrefix.BASE, IdPrefix.TABLE)
        ):
            raise ApiError(
                f"Resource {resource_id} is not valid", HttpErrorCode.RESTRICTED_RESOURCE
            )
        if resource_id.startswith(IdPrefix.SPACE):
            if not space_ids or resource_id not in space_ids:
                raise ApiError(
                    f"You are not allowed to access space {resource_id}",
                    HttpErrorCode.RESTRICTED_RESOURCE,
                )
            cls.set("spaceId", resource_id)
        if resource_id.startswith(IdPrefix.BASE):
            space_id = await self.get_upper_id_by_base_id(resource_id, include_inactive_resource)
            in_range = (space_ids and space_id in space_ids) or (
                base_ids and resource_id in base_ids
            )
            if not in_range:
                raise ApiError(
                    f"You are not allowed to access base {resource_id}",
                    HttpErrorCode.RESTRICTED_RESOURCE,
                )
        if resource_id.startswith(IdPrefix.TABLE):
            base_id, space_id = await self.get_upper_id_by_table_id(
                resource_id, include_inactive_resource
            )
            in_range = (space_ids and space_id in space_ids) or (base_ids and base_id in base_ids)
            if not in_range:
                raise ApiError(
                    f"You are not allowed to access table {resource_id}",
                    HttpErrorCode.RESTRICTED_RESOURCE,
                )
        return scopes

    # -- user role permissions ----------------------------------------------------
    async def _get_permission_by_space_id(
        self, space_id: str, include_inactive_resource: bool = False
    ) -> list[Action]:
        role = await self.get_role_by_space_id(space_id, include_inactive_resource)
        if not role:
            raise ApiError(
                "you have no permission to access this space",
                HttpErrorCode.RESTRICTED_RESOURCE,
            )
        cls.set("spaceId", space_id)
        return get_permissions(role)

    async def get_permission_by_base_id(
        self, base_id: str, include_inactive_resource: bool = False
    ) -> list[Action]:
        temp_auth_base_id = cls.get("tempAuthBaseId")
        if temp_auth_base_id == base_id:
            template = await self._get_template_raw_by_base_id(base_id)
            if template:
                cls.set(
                    "template",
                    {"id": template["id"], "baseId": template["snapshot"]["baseId"]},
                )
                return list(TEMPLATE_PERMISSIONS)
            return get_permissions(Role.OWNER)
        role = await self.get_role_by_base_id(base_id)
        space_role = await self.get_role_by_space_id(
            await self.get_upper_id_by_base_id(base_id, include_inactive_resource),
            include_inactive_resource,
        )
        if not role and not space_role:
            raise ApiError(
                "you have no permission to access this base",
                HttpErrorCode.RESTRICTED_RESOURCE,
            )
        base_permissions = get_permissions(role) if role else []
        space_permissions = get_permissions(space_role) if space_role else []
        # A user can hold concurrent space- and base-level roles; merge to the
        # highest applicable permission level.
        return list(dict.fromkeys([*base_permissions, *space_permissions]))

    async def _get_permission_by_table_id(
        self, table_id: str, include_inactive_resource: bool = False
    ) -> list[Action]:
        base_id, _ = await self.get_upper_id_by_table_id(table_id, include_inactive_resource)
        return await self.get_permission_by_base_id(base_id, include_inactive_resource)

    async def get_permissions_by_resource_id(
        self, resource_id: str, include_inactive_resource: bool = False
    ) -> list[Action]:
        if resource_id.startswith(IdPrefix.SPACE):
            return await self._get_permission_by_space_id(resource_id, include_inactive_resource)
        if resource_id.startswith(IdPrefix.BASE):
            return await self.get_permission_by_base_id(resource_id, include_inactive_resource)
        if resource_id.startswith(IdPrefix.TABLE):
            return await self._get_permission_by_table_id(resource_id, include_inactive_resource)
        raise ApiError("Request path is not valid", HttpErrorCode.RESTRICTED_RESOURCE)

    async def get_permissions_for(
        self,
        resource_id: str,
        access_token_id: str | None = None,
        include_inactive_resource: bool = False,
    ) -> list[Action]:
        user_permissions = await self.get_permissions_by_resource_id(
            resource_id, include_inactive_resource
        )
        if access_token_id:
            token_permissions = await self.get_permissions_by_access_token(
                resource_id, access_token_id, include_inactive_resource
            )
            return [p for p in user_permissions if p in set(token_permissions)]
        return user_permissions

    async def valid_permissions(
        self,
        resource_id: str,
        permissions: list[Action],
        access_token_id: str | None = None,
        include_inactive_resource: bool = False,
    ) -> list[Action]:
        own_permissions = await self.get_permissions_for(
            resource_id, access_token_id, include_inactive_resource
        )
        if all(permission in own_permissions for permission in permissions):
            return own_permissions
        raise ApiError(
            f"not allowed to operate {', '.join(permissions)} on {resource_id}",
            HttpErrorCode.RESTRICTED_RESOURCE,
        )

    # -- template ------------------------------------------------------------------
    async def _get_template_raw_by_base_id(self, base_id: str) -> dict[str, Any] | None:
        async with db_engine.session() as session:
            row = (
                await session.execute(
                    select(_template).where(_template.c.base_id == base_id)
                )
            ).mappings().first()
        if row is None:
            return None
        result = dict(row)
        snapshot = result.get("snapshot")
        result["snapshot"] = json.loads(snapshot) if isinstance(snapshot, str) else (snapshot or {})
        return result

    def get_template_id_by_header(self, template_header: str) -> str | None:
        try:
            return self._jwt.verify(template_header).get("templateId")
        except Exception:
            return None

    def generate_template_header(self, template_id: str) -> str:
        return self._jwt.sign({"templateId": template_id}, expires_in="1d")

    # -- base share ------------------------------------------------------------------
    async def get_base_share_info(self, share_id: str) -> dict[str, Any] | None:
        async with db_engine.session() as session:
            row = (
                await session.execute(
                    select(_base_share).where(
                        _base_share.c.share_id == share_id, _base_share.c.enabled.is_(True)
                    )
                )
            ).mappings().first()
        return dict(row) if row else None

    async def base_share_requires_password(self, share_id: str) -> bool:
        async with db_engine.session() as session:
            row = (
                await session.execute(
                    select(_base_share.c.password).where(
                        _base_share.c.share_id == share_id, _base_share.c.enabled.is_(True)
                    )
                )
            ).first()
        return bool(row and row[0])

    async def validate_base_share_password_token(self, share_id: str, token: str) -> bool:
        try:
            payload = self._jwt.verify(token)
            if payload.get("shareId") != share_id:
                return False
            async with db_engine.session() as session:
                row = (
                    await session.execute(
                        select(_base_share.c.password).where(
                            _base_share.c.share_id == share_id,
                            _base_share.c.enabled.is_(True),
                        )
                    )
                ).first()
            if not row or not row[0]:
                return False
            return payload.get("password") == row[0]
        except Exception:
            return False

    async def get_base_share_permissions(
        self, share_id: str, resource_id: str
    ) -> list[Action]:
        base_share = await self.get_base_share_info(share_id)
        if not base_share:
            raise ApiError(
                f"Base share {share_id} is not found", HttpErrorCode.RESTRICTED_RESOURCE
            )
        base_id = base_share["base_id"]
        node_id = base_share["node_id"]
        if not await self._check_resource_belongs_to_share(resource_id, base_id, node_id):
            raise ApiError(
                f"Resource {resource_id} is not accessible via share {share_id}",
                HttpErrorCode.RESTRICTED_RESOURCE,
            )
        cls.set("baseShare", {"baseId": base_id, "nodeId": node_id})
        if base_share["allow_edit"] and not is_anonymous(cls.get("user.id")):
            return [
                p for p in get_permissions(Role.EDITOR) if p not in SHARE_EXCLUDED_PERMISSIONS
            ]
        permissions = list(TEMPLATE_PERMISSIONS)
        if base_share["allow_copy"]:
            permissions.append("record|copy")
        return permissions

    async def valid_base_share_permissions(
        self, share_id: str, resource_id: str, permissions: list[Action]
    ) -> list[Action]:
        share_permissions = await self.get_base_share_permissions(share_id, resource_id)
        if all(permission in share_permissions for permission in permissions):
            return share_permissions
        message = (
            f"Base share access denied, not allowed to operate "
            f"{', '.join(permissions)} on {resource_id}"
        )
        raise ApiError(message, HttpErrorCode.RESTRICTED_RESOURCE)

    @staticmethod
    def get_base_share_id_by_header(share_header: str) -> str | None:
        if not share_header or not share_header.startswith(IdPrefix.SHARE):
            return None
        return share_header

    async def _check_resource_belongs_to_share(
        self, resource_id: str, base_id: str, node_id: str | None
    ) -> bool:
        prefix = resource_id[:3]
        if prefix == IdPrefix.BASE:
            return resource_id == base_id
        if prefix == IdPrefix.TABLE:
            return await self._check_table_belongs_to_share(resource_id, base_id, node_id)
        if prefix == IdPrefix.VIEW:
            table_id = await self._table_id_of_view(resource_id)
            return table_id is not None and await self._check_table_belongs_to_share(
                table_id, base_id, node_id
            )
        if prefix == IdPrefix.FIELD:
            table_id = await self._table_id_of_field(resource_id)
            return table_id is not None and await self._check_table_belongs_to_share(
                table_id, base_id, node_id
            )
        if prefix == IdPrefix.APP:
            return await self._check_app_belongs_to_share(resource_id, base_id, node_id)
        return False

    async def _table_id_of_view(self, view_id: str) -> str | None:
        async with db_engine.session() as session:
            row = (
                await session.execute(
                    select(_view.c.table_id).where(
                        _view.c.id == view_id, _view.c.deleted_time.is_(None)
                    )
                )
            ).first()
        return row[0] if row else None

    async def _table_id_of_field(self, field_id: str) -> str | None:
        async with db_engine.session() as session:
            row = (
                await session.execute(
                    select(_field.c.table_id).where(
                        _field.c.id == field_id, _field.c.deleted_time.is_(None)
                    )
                )
            ).first()
        return row[0] if row else None

    async def _get_base_nodes(self, base_id: str) -> list[dict[str, Any]]:
        cache: dict[str, list[dict[str, Any]]] = cls.get("baseShareNodeCache") or {}
        if base_id in cache:
            return cache[base_id]
        async with db_engine.session() as session:
            rows = (
                (
                    await session.execute(
                        select(
                            _base_node.c.id,
                            _base_node.c.parent_id,
                            _base_node.c.resource_type,
                            _base_node.c.resource_id,
                        ).where(_base_node.c.base_id == base_id)
                    )
                )
                .mappings()
                .all()
            )
        nodes = [dict(row) for row in rows]
        cache[base_id] = nodes
        cls.set("baseShareNodeCache", cache)
        return nodes

    @staticmethod
    def _collect_descendant_node_ids(
        all_nodes: list[dict[str, Any]], node_id: str
    ) -> set[str]:
        allowed = {node_id}
        frontier = [node_id]
        while frontier:
            current = frontier.pop()
            for node in all_nodes:
                if node["parent_id"] == current and node["id"] not in allowed:
                    allowed.add(node["id"])
                    frontier.append(node["id"])
        return allowed

    async def _is_table_allowed_by_node_id(
        self, base_id: str, table_id: str, node_id: str
    ) -> bool:
        all_nodes = await self._get_base_nodes(base_id)
        node_map = {node["id"]: node for node in all_nodes}
        allowed_ids = self._collect_descendant_node_ids(all_nodes, node_id)
        shared_node = node_map.get(node_id)
        if (
            shared_node
            and shared_node["resource_type"].lower() == "table"
            and shared_node["resource_id"] == table_id
        ):
            return True
        return any(
            (node := node_map.get(allowed_id))
            and node["resource_type"].lower() == "table"
            and node["resource_id"] == table_id
            for allowed_id in allowed_ids
        )

    async def _is_node_allowed_by_node_id(
        self, base_id: str, target_node_id: str, node_id: str
    ) -> bool:
        all_nodes = await self._get_base_nodes(base_id)
        return target_node_id in self._collect_descendant_node_ids(all_nodes, node_id)

    async def _is_table_linked_from_shared_node(
        self, base_id: str, foreign_table_id: str, node_id: str
    ) -> bool:
        all_nodes = await self._get_base_nodes(base_id)
        allowed_ids = self._collect_descendant_node_ids(all_nodes, node_id)
        shared_table_ids = [
            node["resource_id"]
            for node in all_nodes
            if node["id"] in allowed_ids
            and node["resource_type"].lower() == "table"
            and node["resource_id"]
        ]
        if not shared_table_ids:
            return False
        async with db_engine.session() as session:
            rows = (
                await session.execute(
                    select(_field.c.options).where(
                        _field.c.table_id.in_(shared_table_ids),
                        _field.c.type == "link",
                        _field.c.deleted_time.is_(None),
                    )
                )
            ).scalars().all()
        for options in rows:
            try:
                parsed = json.loads(options) if options else None
                if parsed and parsed.get("foreignTableId") == foreign_table_id:
                    return True
            except (json.JSONDecodeError, TypeError):
                continue
        return False

    async def _check_table_belongs_to_share(
        self, table_id: str, base_id: str, node_id: str | None
    ) -> bool:
        async with db_engine.session() as session:
            row = (
                await session.execute(
                    select(_table_meta.c.base_id).where(
                        _table_meta.c.id == table_id, _table_meta.c.deleted_time.is_(None)
                    )
                )
            ).first()
        if not row or row[0] != base_id:
            return False
        if not node_id:
            return True
        if await self._is_table_allowed_by_node_id(base_id, table_id, node_id):
            return True
        # Link field targets stay reachable even outside the shared node subtree
        return await self._is_table_linked_from_shared_node(base_id, table_id, node_id)

    async def _check_app_belongs_to_share(
        self, app_id: str, base_id: str, node_id: str | None
    ) -> bool:
        nodes = await self._get_base_nodes(base_id)
        app_node = next(
            (
                node
                for node in nodes
                if node["resource_type"].lower() == "app" and node["resource_id"] == app_id
            ),
            None,
        )
        if not app_node:
            return False
        if not node_id:
            return True
        return await self._is_node_allowed_by_node_id(base_id, app_node["id"], node_id)

    # -- share view --------------------------------------------------------------------
    async def get_share_view_info(self, share_id: str) -> dict[str, Any] | None:
        async with db_engine.session() as session:
            row = (
                await session.execute(
                    select(
                        _view.c.id,
                        _view.c.table_id,
                        _view.c.type,
                        _view.c.share_meta,
                    ).where(
                        _view.c.share_id == share_id,
                        _view.c.enable_share.is_(True),
                        _view.c.deleted_time.is_(None),
                    )
                )
            ).mappings().first()
        if not row:
            return None
        share_meta = row["share_meta"]
        if isinstance(share_meta, str):
            share_meta = json.loads(share_meta)
        return {
            "shareId": share_id,
            "viewId": row["id"],
            "tableId": row["table_id"],
            "type": row["type"],
            "shareMeta": share_meta,
        }

    async def share_view_requires_password(self, share_id: str) -> bool:
        info = await self.get_share_view_info(share_id)
        return bool(info and (info.get("shareMeta") or {}).get("password"))

    async def validate_share_view_password_token(self, share_id: str, token: str) -> bool:
        try:
            payload = self._jwt.verify(token)
            if payload.get("shareId") != share_id:
                return False
            info = await self.get_share_view_info(share_id)
            password = (info or {}).get("shareMeta", {}).get("password") if info else None
            if not password:
                return False
            return payload.get("password") == password
        except Exception:
            return False

    async def get_share_view_permissions(
        self, share_id: str, resource_id: str
    ) -> list[Action]:
        info = await self.get_share_view_info(share_id)
        if not info:
            raise ApiError(
                f"Share view {share_id} is not found", HttpErrorCode.RESTRICTED_RESOURCE
            )
        if not await self._check_resource_belongs_to_share_view(resource_id, info):
            raise ApiError(
                f"Resource {resource_id} is not accessible via share {share_id}",
                HttpErrorCode.RESTRICTED_RESOURCE,
            )
        cls.set("shareViewId", share_id)
        share_meta = info.get("shareMeta") or {}
        if (
            share_meta.get("allowEdit")
            and share_meta.get("includeRecords")
            and info["type"] in SHARE_VIEW_EDITABLE_TYPES
            and not is_anonymous(cls.get("user.id"))
        ):
            return [
                p for p in SHARE_VIEW_EDIT_PERMISSIONS if p not in SHARE_EXCLUDED_PERMISSIONS
            ]
        permissions = list(TEMPLATE_PERMISSIONS)
        if share_meta.get("allowCopy"):
            permissions.append("record|copy")
        return permissions

    async def valid_share_view_permissions(
        self, share_id: str, resource_id: str, permissions: list[Action]
    ) -> list[Action]:
        share_permissions = await self.get_share_view_permissions(share_id, resource_id)
        if all(permission in share_permissions for permission in permissions):
            return share_permissions
        message = (
            f"Share view access denied, not allowed to operate "
            f"{', '.join(permissions)} on {resource_id}"
        )
        raise ApiError(message, HttpErrorCode.RESTRICTED_RESOURCE)

    async def _check_resource_belongs_to_share_view(
        self, resource_id: str, info: dict[str, Any]
    ) -> bool:
        if resource_id in (info["tableId"], info["viewId"]):
            return True
        prefix = resource_id[:3]
        if prefix == IdPrefix.TABLE:
            return resource_id == info["tableId"]
        if prefix == IdPrefix.VIEW:
            return resource_id == info["viewId"]
        if prefix == IdPrefix.FIELD:
            table_id = await self._table_id_of_field(resource_id)
            return table_id == info["tableId"]
        return False

    get_share_view_id_by_header = get_base_share_id_by_header


# ---------------------------------------------------------------------------
# permission guard (permission.guard.ts port)
# ---------------------------------------------------------------------------


class PermissionGuard:
    def __init__(self, permission_service: PermissionService | None = None) -> None:
        self._permissions = permission_service or PermissionService()

    # -- resource id derivation ------------------------------------------------
    @staticmethod
    def _default_resource_id(request: Request) -> str | None:
        # before check baseId, as users can be individually invited into the base
        params = request.path_params
        return params.get("baseId") or params.get("spaceId") or params.get("tableId")

    @staticmethod
    async def _resource_id_from_meta(request: Request, endpoint: Any) -> str | None:
        meta = _get_meta(endpoint, "resourceMeta")
        if not meta:
            return None
        position = meta["position"]
        if position == "params":
            return request.path_params.get(meta["type"])
        if position == "query":
            return request.query_params.get(meta["type"])
        if position == "body":
            body = await request.json()
            return body.get(meta["type"]) if isinstance(body, dict) else None
        return None

    async def _get_resource_id(self, request: Request, endpoint: Any) -> str | None:
        return await self._resource_id_from_meta(request, endpoint) or self._default_resource_id(
            request
        )

    # -- token pre-checks ---------------------------------------------------------
    async def _token_scope_check(self, scope: Action) -> bool:
        access_token_id = cls.get("accessTokenId")
        if access_token_id:
            token = await self._permissions.get_access_token(access_token_id)
            return scope in token["scopes"]
        return True

    async def _instance_permission_check(self, action: Action) -> bool:
        if not cls.get("user.isAdmin"):
            raise ApiError(
                "User is not an admin",
                HttpErrorCode.RESTRICTED_RESOURCE,
                {"localization": {"i18nKey": "httpErrors.permission.userNotAdmin"}},
            )
        access_token_id = cls.get("accessTokenId")
        if access_token_id:
            token = await self._permissions.get_access_token(access_token_id)
            if action not in token["scopes"]:
                raise ApiError(
                    f"Access token does not have {action} permission",
                    HttpErrorCode.RESTRICTED_RESOURCE,
                    {"localization": {"i18nKey": "httpErrors.permission.accessTokenNoPermission"}},
                )
        return True

    async def _resource_permission(
        self, resource_id: str | None, permissions: list[Action]
    ) -> bool:
        if not resource_id:
            raise ApiError(
                "Permission check ID does not exist",
                HttpErrorCode.RESTRICTED_RESOURCE,
                {"localization": {"i18nKey": "httpErrors.permission.checkIdNotExist"}},
            )
        own_permissions = await self._permissions.valid_permissions(
            resource_id, permissions, cls.get("accessTokenId")
        )
        cls.set("permissions", own_permissions)
        return True

    async def _check_permissions(
        self, resource_id: str | None, permissions: list[Action]
    ) -> bool:
        if "instance|update" in permissions:
            return await self._instance_permission_check("instance|update")
        if "instance|read" in permissions:
            return await self._instance_permission_check("instance|read")
        if "space|create" in permissions:
            return await self._token_scope_check("space|create")
        if "base|read_all" in permissions:
            return await self._token_scope_check("base|read_all")
        if not resource_id and "space|read" in permissions:
            return await self._token_scope_check("space|read")
        if "user|integrations" in permissions:
            return await self._token_scope_check("user|integrations")
        return await self._resource_permission(resource_id, permissions)

    async def _permission_check(self, request: Request, endpoint: Any) -> bool:
        permissions = _get_meta(endpoint, "permissions")
        any_permissions = _get_meta(endpoint, "anyPermissions")
        resource_id = await self._get_resource_id(request, endpoint)
        access_token_id = cls.get("accessTokenId")
        if access_token_id and not permissions:
            # Tokens may only reach permission-restricted or token-opted-in routes
            return bool(_get_meta(endpoint, "isTokenAccess"))
        if not permissions:
            return True
        if any_permissions:
            try:
                return await self._check_permissions(resource_id, permissions)
            except Exception as error:
                for group in any_permissions:
                    try:
                        return await self._check_permissions(resource_id, group)
                    except Exception:
                        continue
                raise error
        return await self._check_permissions(resource_id, permissions)

    # -- share / template paths ------------------------------------------------------
    def _ensure_anonymous_fallback_user(self) -> None:
        current_user_id = cls.get("user.id")
        if not current_user_id or is_anonymous(current_user_id):
            cls.set(
                "user",
                {"id": ANONYMOUS_USER_ID, "name": ANONYMOUS_USER_ID, "email": ""},
            )

    async def _template_permission_check(
        self, request: Request, endpoint: Any, template_header: str | None
    ) -> bool:
        if template_header:
            template_id = self._permissions.get_template_id_by_header(template_header)
            if not template_id:
                code = (
                    HttpErrorCode.UNAUTHORIZED
                    if is_anonymous(cls.get("user.id"))
                    else HttpErrorCode.RESTRICTED_RESOURCE
                )
                raise ApiError("Template header is invalid", code)
        resource_id = await self._get_resource_id(request, endpoint)
        if not resource_id:
            code = (
                HttpErrorCode.UNAUTHORIZED
                if is_anonymous(cls.get("user.id"))
                else HttpErrorCode.RESTRICTED_RESOURCE
            )
            raise ApiError("Template permission check ID does not exist", code)
        permissions = _get_meta(endpoint, "permissions")
        if not permissions:
            raise ApiError("Template permissions are required", HttpErrorCode.RESTRICTED_RESOURCE)
        own_permissions = await self._valid_template_permissions(resource_id, permissions)
        cls.set("permissions", own_permissions)
        return True

    async def _valid_template_permissions(
        self, resource_id: str, permissions: list[Action]
    ) -> list[Action]:
        template = cls.get("template")
        if template:
            template_permissions: list[Action] = list(TEMPLATE_PERMISSIONS)
        else:
            template_permissions = await self._get_template_permissions(resource_id)
        if all(permission in template_permissions for permission in permissions):
            return template_permissions
        message = (
            f"Template access denied, not allowed to operate "
            f"{', '.join(permissions)} on {resource_id}"
        )
        raise ApiError(message, HttpErrorCode.RESTRICTED_RESOURCE)

    async def _get_template_permissions(self, resource_id: str) -> list[Action]:
        code = (
            HttpErrorCode.UNAUTHORIZED
            if is_anonymous(cls.get("user.id"))
            else HttpErrorCode.RESTRICTED_RESOURCE
        )
        denied = ApiError(
            f"Template access denied, template not found for {resource_id}", code
        )
        if resource_id.startswith(IdPrefix.BASE):
            template = await self._permissions._get_template_raw_by_base_id(resource_id)
            if not template or not template.get("id"):
                raise denied
            cls.set(
                "template", {"id": template["id"], "baseId": template["snapshot"]["baseId"]}
            )
        elif resource_id.startswith(IdPrefix.TABLE):
            async with db_engine.session() as session:
                row = (
                    await session.execute(
                        select(_table_meta.c.base_id).where(
                            _table_meta.c.id == resource_id,
                            _table_meta.c.deleted_time.is_(None),
                        )
                    )
                ).first()
            if not row:
                raise denied
            template = await self._permissions._get_template_raw_by_base_id(row[0])
            if not template:
                raise denied
            cls.set(
                "template", {"id": template["id"], "baseId": template["snapshot"]["baseId"]}
            )
        else:
            raise ApiError(
                f"Resource {resource_id} is not valid for template", code
            )
        return list(TEMPLATE_PERMISSIONS)

    async def _ensure_share_auth_cookie(
        self,
        request: Request,
        share_id: str,
        requires_password: bool,
        validate: Any,
    ) -> None:
        if not requires_password:
            return
        from .session import parse_cookie_header

        cookies = parse_cookie_header(request.headers.get("cookie"))
        token = cookies.get(share_id)
        if not token:
            raise ApiError("Unauthorized", HttpErrorCode.UNAUTHORIZED_SHARE)
        if not await validate(share_id, token):
            raise ApiError("Unauthorized", HttpErrorCode.UNAUTHORIZED_SHARE)

    async def _base_share_permission_check(
        self, request: Request, endpoint: Any, share_id: str
    ) -> bool:
        await self._ensure_share_auth_cookie(
            request,
            share_id,
            await self._permissions.base_share_requires_password(share_id),
            self._permissions.validate_base_share_password_token,
        )
        resource_id = await self._get_resource_id(request, endpoint)
        if not resource_id:
            code = (
                HttpErrorCode.UNAUTHORIZED
                if is_anonymous(cls.get("user.id"))
                else HttpErrorCode.RESTRICTED_RESOURCE
            )
            raise ApiError("Base share permission check ID does not exist", code)
        permissions = _get_meta(endpoint, "permissions")
        if not permissions:
            raise ApiError(
                "Base share permissions are required", HttpErrorCode.RESTRICTED_RESOURCE
            )
        own_permissions = await self._permissions.valid_base_share_permissions(
            share_id, resource_id, permissions
        )
        self._ensure_anonymous_fallback_user()
        cls.set("permissions", own_permissions)
        return True

    async def _share_view_permission_check(
        self, request: Request, endpoint: Any, share_id: str
    ) -> bool:
        await self._ensure_share_auth_cookie(
            request,
            share_id,
            await self._permissions.share_view_requires_password(share_id),
            self._permissions.validate_share_view_password_token,
        )
        resource_id = await self._get_resource_id(request, endpoint)
        if not resource_id:
            code = (
                HttpErrorCode.UNAUTHORIZED
                if is_anonymous(cls.get("user.id"))
                else HttpErrorCode.RESTRICTED_RESOURCE
            )
            raise ApiError("Share view permission check ID does not exist", code)
        permissions = _get_meta(endpoint, "permissions")
        if not permissions:
            raise ApiError(
                "Share view permissions are required", HttpErrorCode.RESTRICTED_RESOURCE
            )
        own_permissions = await self._permissions.valid_share_view_permissions(
            share_id, resource_id, permissions
        )
        self._ensure_anonymous_fallback_user()
        cls.set("permissions", own_permissions)
        return True

    async def _try_base_share_permission_check(
        self, request: Request, endpoint: Any, base_share_header: str | None
    ) -> bool | None:
        if not base_share_header:
            return None
        share_id = self._permissions.get_base_share_id_by_header(base_share_header)
        if not share_id:
            return None
        # Skip share path for endpoints without declared permissions
        if not _get_meta(endpoint, "permissions"):
            return None
        # Skip share check when the target resource is outside the share scope
        resource_id = await self._get_resource_id(request, endpoint)
        if not resource_id or resource_id.startswith(IdPrefix.SPACE):
            return None
        return await self._base_share_permission_check(request, endpoint, share_id)

    async def _try_share_view_permission_check(
        self, request: Request, endpoint: Any, share_view_header: str | None
    ) -> bool | None:
        if not share_view_header:
            return None
        share_id = self._permissions.get_share_view_id_by_header(share_view_header)
        if not share_id:
            return None
        permissions = _get_meta(endpoint, "permissions")
        if not permissions:
            return None
        # The share-view header is only a write sandbox for the common table surface
        method = request.method
        path = request.url.path
        allowed_method = method in ("POST", "PATCH", "DELETE")
        matched_rule = next(
            (rule for rule in SHARE_VIEW_ENDPOINT_RULES if rule[0].search(path)), None
        )
        allowed_permissions = (
            all(permission in matched_rule[1] for permission in permissions)
            if matched_rule
            else False
        )
        if not allowed_method or not matched_rule or not allowed_permissions:
            raise ApiError(
                "This endpoint cannot be used with X-Tea-Share-View",
                HttpErrorCode.RESTRICTED_RESOURCE,
            )
        resource_id = await self._get_resource_id(request, endpoint)
        if not resource_id or resource_id.startswith(IdPrefix.SPACE):
            return None
        return await self._share_view_permission_check(request, endpoint, share_id)

    async def _resolve_resource_permission(
        self,
        request: Request,
        endpoint: Any,
        base_share_header: str | None,
        share_view_header: str | None,
        template_header: str | None,
    ) -> bool | None:
        if base_share_header:
            result = await self._try_base_share_permission_check(
                request, endpoint, base_share_header
            )
            if result is not None:
                return result
        if share_view_header:
            result = await self._try_share_view_permission_check(
                request, endpoint, share_view_header
            )
            if result is not None:
                return result
        if template_header:
            return await self._template_permission_check(request, endpoint, template_header)
        return None

    async def _resolve_anonymous_permission(
        self, request: Request, endpoint: Any, allow_anonymous_type: AllowAnonymousType | None
    ) -> bool:
        if allow_anonymous_type == AllowAnonymousType.PUBLIC:
            return await self._template_permission_check(request, endpoint, None)
        if allow_anonymous_type == AllowAnonymousType.USER:
            return True
        raise ApiError("Unauthorized", HttpErrorCode.UNAUTHORIZED)

    async def _resolve_public_fallback(
        self,
        request: Request,
        endpoint: Any,
        base_share_header: str | None,
        share_view_header: str | None,
        original_error: Exception,
    ) -> bool:
        if base_share_header:
            share_id = self._permissions.get_base_share_id_by_header(base_share_header)
            if share_id:
                try:
                    return await self._base_share_permission_check(request, endpoint, share_id)
                except Exception:
                    pass
        if share_view_header:
            share_id = self._permissions.get_share_view_id_by_header(share_view_header)
            if share_id:
                try:
                    return await self._share_view_permission_check(request, endpoint, share_id)
                except Exception:
                    pass
        try:
            return await self._template_permission_check(request, endpoint, None)
        except Exception:
            raise original_error from None

    async def can_activate(self, request: Request) -> bool:
        endpoint = route_endpoint(request)
        if _get_meta(endpoint, "isPublic"):
            return True
        if _get_meta(endpoint, "isDisabledPermission"):
            return True

        template_header = request.headers.get(TEMPLATE_HEADER.lower()) or request.headers.get(
            TEMPLATE_HEADER
        )
        base_share_header = request.headers.get(
            BASE_SHARE_ID_HEADER.lower()
        ) or request.headers.get(BASE_SHARE_ID_HEADER)
        share_view_header = request.headers.get(
            SHARE_VIEW_ID_HEADER.lower()
        ) or request.headers.get(SHARE_VIEW_ID_HEADER)
        allow_anonymous_type = _get_meta(endpoint, "isAllowAnonymous")

        # 1. RESOURCE-level: exclusively resource-specific auth (base share > share
        #    view > template)
        if allow_anonymous_type == AllowAnonymousType.RESOURCE:
            result = await self._resolve_resource_permission(
                request, endpoint, base_share_header, share_view_header, template_header
            )
            if result is not None:
                return result

        # 2. Share link — permissions are bounded by the link, regardless of role
        if base_share_header:
            result = await self._try_base_share_permission_check(
                request, endpoint, base_share_header
            )
            if result is not None:
                return result
        if share_view_header:
            result = await self._try_share_view_permission_check(
                request, endpoint, share_view_header
            )
            if result is not None:
                return result

        # 3. Anonymous user handling
        if is_anonymous(cls.get("user.id")):
            return await self._resolve_anonymous_permission(
                request, endpoint, allow_anonymous_type
            )

        # 4. Authenticated user: standard check, with PUBLIC fallback
        try:
            return await self._permission_check(request, endpoint)
        except Exception as error:
            if allow_anonymous_type != AllowAnonymousType.PUBLIC:
                raise
            return await self._resolve_public_fallback(
                request, endpoint, base_share_header, share_view_header, error
            )


async def permission_guard(request: Request) -> None:
    """FastAPI dependency: the global PermissionGuard.

    Nest denies by returning ``false`` from ``canActivate``, which Nest turns
    into a 403 ``ForbiddenException('Forbidden resource')``. FastAPI ignores a
    dependency's return value, so a ``False`` verdict here must be raised
    explicitly — otherwise the request would silently proceed (e.g. a scoped
    PAT reaching a route that carries no ``@permissions``).
    """
    if not await PermissionGuard().can_activate(request):
        raise ApiError("Forbidden resource", HttpErrorCode.RESTRICTED_RESOURCE)
