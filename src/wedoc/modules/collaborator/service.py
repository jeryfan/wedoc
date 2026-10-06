"""Collaborator domain service.

Ports features/collaborator/collaborator.service.ts (space-facing surface).
Used by the space module now and the base module later.
"""

import json
from datetime import UTC, datetime
from typing import Any

from ...core import cls
from ...core.errors import ApiError, HttpErrorCode
from ...core.ids import random_string
from ...core.security.permissions import Role, can_manage_role, get_max_level_role
from ..space import repository

PRINCIPAL_USER = "user"
RESOURCE_SPACE = "space"
RESOURCE_BASE = "base"


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _public_avatar(avatar: str | None) -> str | None:
    if not avatar:
        return None
    from ...core.storage import get_public_full_storage_url

    return get_public_full_storage_url(avatar)


class CollaboratorService:
    async def create_space_collaborator(
        self,
        collaborators: list[dict[str, str]],
        space_id: str,
        role: str,
        created_by: str | None = None,
    ) -> dict[str, int]:
        current_user_id = created_by or cls.get("user.id")
        principal_ids = [c["principalId"] for c in collaborators]
        principal_types = [c["principalType"] for c in collaborators]
        exist = await repository.count_collaborators(
            principal_ids, principal_types, space_id, RESOURCE_SPACE
        )
        if exist:
            raise ApiError(
                "Collaborator has already existed in space",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.collaborator.alreadyExisted"}},
            )
        # A principal joining at space level drops their per-base rows.
        base_ids = await repository.list_base_ids_by_space(space_id)
        if base_ids:
            await repository.delete_collaborators_by_resource_ids(
                principal_ids, principal_types, base_ids
            )
        now = datetime.now(UTC).replace(tzinfo=None)
        await repository.insert_collaborators(
            [
                {
                    "id": random_string(16),
                    "resource_id": space_id,
                    "resource_type": RESOURCE_SPACE,
                    "role_name": role,
                    "principal_id": c["principalId"],
                    "principal_type": c["principalType"],
                    "created_by": current_user_id,
                    "created_time": now,
                }
                for c in collaborators
            ]
        )
        return {"count": len(collaborators)}

    # -- listing --------------------------------------------------------------

    async def _space_tree_rows(
        self, space_id: str, include_base: bool
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, str]]]:
        base_ids = await repository.list_base_ids_by_space(space_id) if include_base else []
        rows = await repository.list_collaborator_rows([space_id, *base_ids])
        users = await repository.list_user_rows_by_ids(list({r["principal_id"] for r in rows}))
        user_map = {u["id"]: u for u in users}
        base_map = {
            b["id"]: {"name": b["name"], "id": b["id"]}
            for b in await repository.list_base_rows_by_space(space_id)
        }
        return rows, user_map, base_map

    async def get_space_collaborator_stats(
        self, space_id: str, options: dict[str, Any]
    ) -> dict[str, int]:
        include_base = bool(options.get("includeBase"))
        rows, user_map, _ = await self._space_tree_rows(space_id, include_base)
        visible = self._filter_rows(rows, user_map, options)
        total = len(visible)
        uniq = len({r["principal_id"] for r in visible})
        return {"total": total, "uniqTotal": uniq}

    @staticmethod
    def _filter_rows(
        rows: list[dict[str, Any]],
        user_map: dict[str, dict[str, Any]],
        options: dict[str, Any],
    ) -> list[dict[str, Any]]:
        include_system = options.get("includeSystem")
        search = (options.get("search") or "").lower()
        principal_type = options.get("type")
        principal_id = options.get("principalId")
        out = []
        for row in rows:
            user = user_map.get(row["principal_id"])
            is_system = bool(user and user.get("is_system"))
            if not include_system and is_system:
                continue
            if principal_type and row["principal_type"] != principal_type:
                continue
            if principal_id and row["principal_id"] != principal_id:
                continue
            if search:
                name = (user or {}).get("name") or ""
                email = (user or {}).get("email") or ""
                if search not in name.lower() and search not in email.lower():
                    continue
            out.append(row)
        return out

    def _map_list_item(
        self, row: dict[str, Any], user: dict[str, Any] | None, base_map: dict[str, Any]
    ) -> dict[str, Any]:
        item: dict[str, Any] = {
            "type": PRINCIPAL_USER,
            "resourceType": row["resource_type"],
            "userId": row["principal_id"],
            "userName": (user or {}).get("name"),
            "email": (user or {}).get("email"),
            "avatar": _public_avatar((user or {}).get("avatar")),
            "role": row["role_name"],
            "createdTime": _iso(row["created_time"]),
            "lastSignTime": _iso((user or {}).get("last_sign_time")),
            "billable": True,
        }
        base = base_map.get(row["resource_id"])
        if base:
            item["base"] = base
        return item

    async def get_list_by_space(
        self, space_id: str, options: dict[str, Any]
    ) -> list[dict[str, Any]]:
        include_base = bool(options.get("includeBase"))
        rows, user_map, base_map = await self._space_tree_rows(space_id, include_base)
        visible = [
            r for r in self._filter_rows(rows, user_map, options) if r["principal_id"] in user_map
        ]
        reverse = (options.get("orderBy") or "desc") == "desc"
        # ref orders by created_time (desc|asc) with principal_id asc as the
        # pagination tiebreak in both directions.
        visible.sort(key=lambda r: r["principal_id"])
        visible.sort(key=lambda r: r["created_time"], reverse=reverse)
        skip = options.get("skip") or 0
        take = options.get("take")
        paged = visible[skip:] if take is None else visible[skip : skip + take]
        return [
            self._map_list_item(row, user_map.get(row["principal_id"]), base_map) for row in paged
        ]

    async def get_unique_list_by_space(
        self, space_id: str, options: dict[str, Any]
    ) -> dict[str, Any]:
        include_system = bool(options.get("includeSystem"))
        rows, user_map, _ = await self._space_tree_rows(space_id, include_base=True)
        groups: dict[str, dict[str, Any]] = {}
        for row in self._filter_rows(rows, user_map, {**options, "includeBase": True}):
            user = user_map.get(row["principal_id"])
            if user is None:
                continue  # excludeDanglingUniquePrincipals
            if not include_system and user.get("is_system"):
                continue
            group = groups.setdefault(
                row["principal_id"],
                {"user": user, "space_role": None, "base_count": 0, "created_time": None},
            )
            if row["resource_type"] == RESOURCE_SPACE:
                group["space_role"] = row["role_name"]
            elif row["resource_type"] == RESOURCE_BASE:
                group["base_count"] += 1
            created = row["created_time"]
            if group["created_time"] is None or created < group["created_time"]:
                group["created_time"] = created
        reverse = (options.get("orderBy") or "desc") == "desc"
        ordered = sorted(groups.items(), key=lambda kv: kv[0])
        ordered.sort(key=lambda kv: kv[1]["created_time"], reverse=reverse)
        skip = options.get("skip") or 0
        take = options.get("take") or 50
        page = ordered[skip : skip + take]
        collaborators = []
        for _, group in page:
            user = group["user"]
            item: dict[str, Any] = {
                "type": PRINCIPAL_USER,
                "userId": user["id"],
                "userName": user["name"],
                "email": user["email"],
                "avatar": _public_avatar(user.get("avatar")),
                "lastSignTime": _iso(user.get("last_sign_time")),
                "spaceRole": group["space_role"],
                "baseCount": group["base_count"],
                "createdTime": _iso(group["created_time"]),
                "billable": True,
            }
            if user.get("is_system"):
                item["isSystem"] = True
            collaborators.append(item)
        return {"collaborators": collaborators, "total": len(groups)}

    # -- current user's collaborator view --------------------------------------

    async def get_current_user_collaborators_base_and_space_array(
        self,
    ) -> dict[str, Any]:
        user_id = cls.get("user.id")
        rows = await repository.list_collaborator_rows_by_principals([user_id])
        role_map: dict[str, str] = {}
        base_ids: list[str] = []
        space_ids: list[str] = []
        for row in rows:
            current = role_map.get(row["resource_id"])
            if current is None or can_manage_role(row["role_name"], current):
                role_map[row["resource_id"]] = row["role_name"]
            if row["resource_type"] == RESOURCE_BASE:
                base_ids.append(row["resource_id"])
            elif row["resource_type"] == RESOURCE_SPACE:
                space_ids.append(row["resource_id"])
        return {"baseIds": base_ids, "spaceIds": space_ids, "roleMap": role_map}

    # -- guards -----------------------------------------------------------------

    async def is_unique_owner_user(self, space_id: str, user_id: str) -> bool:
        rows = await repository.list_collaborator_rows([space_id], resource_type=RESOURCE_SPACE)
        owner_ids = {
            r["principal_id"]
            for r in rows
            if r["role_name"] == Role.OWNER and r["principal_type"] == PRINCIPAL_USER
        }
        if len(owner_ids) != 1:
            return False
        owner = owner_ids.pop()
        users = await repository.list_user_rows_by_ids([owner])
        user = users[0] if users else None
        if user is None or user.get("is_system") or user.get("deleted_time") is not None:
            return False
        return owner == user_id

    async def validate_user_add_role(
        self, user_id: str, add_role: str, resource_id: str, resource_type: str
    ) -> None:
        if resource_type == RESOURCE_BASE:
            from sqlalchemy import select

            from ...db import engine as db_engine
            from ...db.models_meta import Base as BaseModel

            async with db_engine.session() as session:
                row = (
                    await session.execute(
                        select(BaseModel).where(
                            BaseModel.id == resource_id, BaseModel.deleted_time.is_(None)
                        )
                    )
                ).scalar_one_or_none()
            if row is None:
                raise ApiError(
                    "Base not found",
                    HttpErrorCode.VALIDATION_ERROR,
                    {"localization": {"i18nKey": "httpErrors.collaborator.baseNotFound"}},
                )
            space_id = row.space_id
        else:
            space_id = resource_id
        rows = await repository.list_collaborators_by_principals_and_resources(
            [user_id], [space_id, resource_id]
        )
        if not rows:
            raise ApiError(
                "User not found in collaborator",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.collaborator.userNotFoundInCollaborator"}},
            )
        user_role = get_max_level_role(rows)
        if user_role == add_role:
            return
        if not can_manage_role(user_role, add_role):
            raise ApiError(
                f"You do not have permission to add this role collaborator: {add_role}",
                HttpErrorCode.RESTRICTED_RESOURCE,
                {"localization": {"i18nKey": "httpErrors.collaborator.noPermissionToAddRole"}},
            )

    async def add_space_collaborators(
        self, space_id: str, collaborators: list[dict[str, str]], role: str
    ) -> dict[str, int]:
        user_id = cls.get("user.id")
        await self.validate_user_add_role(user_id, role, space_id, RESOURCE_SPACE)
        user_ids = [c["principalId"] for c in collaborators if c["principalType"] == "user"]
        await self._validate_collaborator_users(user_ids)
        result = await self.create_space_collaborator(
            collaborators=collaborators, space_id=space_id, role=role, created_by=user_id
        )
        await self._notify_collaborator_invite(
            user_id, user_ids, RESOURCE_SPACE, space_id
        )
        return result

    async def _notify_collaborator_invite(
        self, from_user_id: str, to_user_ids: list[str], resource_type: str, resource_id: str
    ) -> None:
        """Emit a collaboratorInvite notification to each freshly added user
        (ports CollaboratorInvitedEvent -> notification.service)."""
        recipients = [uid for uid in to_user_ids if uid and uid != from_user_id]
        if not recipients:
            return
        from ..notification import repository as notify_repository
        from ..notification.service import NotificationService

        if resource_type == RESOURCE_SPACE:
            space = await repository.get_space_row(resource_id)
            resource_name = space["name"] if space else ""
            url_path = f"/space/{resource_id}"
            i18n_key = "email.templates.notify.collaboratorInvite.space"
        else:
            from ..base import repository as base_repository

            base = await base_repository.get_base_row(resource_id, include_deleted=True)
            resource_name = base["name"] if base else ""
            url_path = f"/base/{resource_id}"
            i18n_key = "email.templates.notify.collaboratorInvite.base"
        from_user = (await notify_repository.get_users_by_ids([from_user_id])).get(from_user_id)
        from_name = (from_user or {}).get("name") or ""
        noun = "space" if resource_type == RESOURCE_SPACE else "project"
        message = f"{from_name} invited you to join the {noun} {resource_name}"
        message_i18n = json.dumps(
            {
                "i18nKey": i18n_key,
                "context": {"fromUserName": from_name, "resourceName": resource_name},
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        service = NotificationService()
        for uid in recipients:
            await service.create_and_push(
                from_user_id=from_user_id,
                to_user_id=uid,
                notify_type="collaboratorInvite",
                message=message,
                message_i18n=message_i18n,
                url_path=url_path,
            )

    @staticmethod
    async def _validate_collaborator_users(user_ids: list[str]) -> None:
        if not user_ids:
            return
        users = await repository.list_user_rows_by_ids(user_ids)
        found = {u["id"] for u in users if u["deleted_time"] is None}
        diff = [uid for uid in user_ids if uid not in found]
        if diff:
            raise ApiError(
                f"User not found: {', '.join(diff)}",
                HttpErrorCode.VALIDATION_ERROR,
                {
                    "localization": {
                        "i18nKey": "httpErrors.collaborator.userNotFound",
                        "context": {"userIds": ", ".join(diff)},
                    }
                },
            )

    # -- mutations ---------------------------------------------------------------

    async def _operator_collaborators(
        self,
        resource_id: str,
        resource_type: str,
        target_principal_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        current_user_id = cls.get("user.id")
        resource_ids = [resource_id]
        if resource_type == RESOURCE_BASE:
            from sqlalchemy import select

            from ...db import engine as db_engine
            from ...db.models_meta import Base as BaseModel

            async with db_engine.session() as session:
                row = (
                    await session.execute(
                        select(BaseModel).where(
                            BaseModel.id == resource_id, BaseModel.deleted_time.is_(None)
                        )
                    )
                ).scalar_one_or_none()
            if row is None:
                raise ApiError("Base not found", HttpErrorCode.NOT_FOUND)
            resource_ids = [resource_id, row.space_id]
        rows = await repository.list_collaborator_rows(
            resource_ids, principal_ids=[current_user_id, target_principal_id]
        )
        current_coll = next((r for r in rows if r["principal_id"] == current_user_id), None)
        target_coll = next((r for r in rows if r["principal_id"] == target_principal_id), None)
        if current_coll is None or target_coll is None:
            raise ApiError(
                "User not found in collaborator",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.collaborator.userNotFoundInCollaborator"}},
            )
        return current_coll, target_coll

    async def update_collaborator(
        self,
        resource_id: str,
        resource_type: str,
        principal_id: str,
        principal_type: str,
        role: str,
    ) -> None:
        current_user_id = cls.get("user.id")
        current_coll, target_coll = await self._operator_collaborators(
            resource_id, resource_type, principal_id
        )
        if (
            current_user_id != principal_id
            and current_coll["role_name"] != target_coll["role_name"]
            and not can_manage_role(current_coll["role_name"], target_coll["role_name"])
        ):
            raise ApiError(
                f"You do not have permission to operator this collaborator: {principal_id}",
                HttpErrorCode.RESTRICTED_RESOURCE,
                {"localization": {"i18nKey": "httpErrors.collaborator.noPermissionToUpdate"}},
            )
        if role != current_coll["role_name"] and not can_manage_role(
            current_coll["role_name"], role
        ):
            raise ApiError(
                f"You do not have permission to operator this role: {role}",
                HttpErrorCode.RESTRICTED_RESOURCE,
                {"localization": {"i18nKey": "httpErrors.collaborator.noPermissionToOperateRole"}},
            )
        await repository.update_collaborator_role(
            resource_id, resource_type, principal_id, principal_type, role, current_user_id
        )

    async def delete_collaborator(
        self,
        resource_id: str,
        resource_type: str,
        principal_id: str,
        principal_type: str,
    ) -> dict[str, Any] | None:
        current_user_id = cls.get("user.id")
        current_coll, target_coll = await self._operator_collaborators(
            resource_id, resource_type, principal_id
        )
        if (
            current_user_id != principal_id
            and current_coll["role_name"] != Role.OWNER
            and not can_manage_role(current_coll["role_name"], target_coll["role_name"])
        ):
            raise ApiError(
                "You do not have permission to delete this collaborator",
                HttpErrorCode.RESTRICTED_RESOURCE,
                {"localization": {"i18nKey": "httpErrors.collaborator.noPermissionToDelete"}},
            )
        return await repository.delete_collaborator_row(
            resource_id, resource_type, principal_id, principal_type
        )

    async def delete_base_collaborators_by_space(
        self, space_id: str, principal_id: str, principal_type: str
    ) -> None:
        base_ids = await repository.list_base_ids_by_space(space_id)
        rows = await repository.list_collaborator_rows(
            base_ids, principal_ids=[principal_id], resource_type=RESOURCE_BASE
        )
        for row in rows:
            await self.delete_collaborator(
                row["resource_id"],
                RESOURCE_BASE,
                principal_id,
                principal_type,
            )

    # -- base-facing surface -----------------------------------------------------

    async def get_shared_base(self) -> list[dict[str, Any]]:
        user_id = cls.get("user.id")
        rows = await repository.list_collaborator_rows_by_principals([user_id])
        base_rows = [r for r in rows if r["resource_type"] == RESOURCE_BASE]
        if not base_rows:
            return []
        role_map: dict[str, str] = {}
        base_ids: list[str] = []
        for row in base_rows:
            current = role_map.get(row["resource_id"])
            if current is None or can_manage_role(row["role_name"], current):
                role_map[row["resource_id"]] = row["role_name"]
            base_ids.append(row["resource_id"])
        from ..base import repository as base_repository

        bases = await base_repository.list_base_rows_by_ids(base_ids)
        if not bases:
            return []
        space_ids = list({b["space_id"] for b in bases})
        spaces = await repository.list_space_rows_by_ids(space_ids)
        space_map = {s["id"]: s["name"] for s in spaces}
        users = await repository.list_user_rows_by_ids(list({b["created_by"] for b in bases}))
        user_map = {u["id"]: u for u in users}
        result = []
        for base in bases:
            created_user = user_map.get(base["created_by"])
            result.append(
                {
                    "id": base["id"],
                    "name": base["name"],
                    "role": role_map[base["id"]],
                    "icon": base["icon"],
                    "spaceId": base["space_id"],
                    "spaceName": space_map.get(base["space_id"]),
                    "collaboratorType": RESOURCE_BASE,
                    "lastModifiedTime": _iso(base["last_modified_time"]),
                    "createdTime": _iso(base["created_time"]),
                    "createdBy": base["created_by"],
                    "createdUser": {
                        "id": created_user["id"],
                        "name": created_user["name"],
                        "avatar": _public_avatar(created_user.get("avatar")),
                    }
                    if created_user
                    else None,
                }
            )
        return result

    async def _base_tree_rows(
        self, base_id: str
    ) -> tuple[str, list[dict[str, Any]], dict[str, dict[str, Any]]]:
        from ..base import repository as base_repository

        base = await base_repository.get_base_row(base_id)
        if base is None:
            raise ApiError("Base not found", HttpErrorCode.NOT_FOUND)
        rows = await repository.list_collaborator_rows([base_id, base["space_id"]])
        users = await repository.list_user_rows_by_ids(list({r["principal_id"] for r in rows}))
        user_map = {u["id"]: u for u in users}
        return base["space_id"], rows, user_map

    @staticmethod
    def _filter_base_rows(
        rows: list[dict[str, Any]],
        user_map: dict[str, dict[str, Any]],
        options: dict[str, Any],
        require_user: bool = True,
    ) -> list[dict[str, Any]]:
        include_system = options.get("includeSystem")
        search = (options.get("search") or "").lower()
        principal_type = options.get("type")
        roles = options.get("role")
        out = []
        for row in rows:
            user = user_map.get(row["principal_id"])
            # whereNotNull('users.id'): the list/user views drop non-user rows;
            # getTotalBase keeps them (leftJoin without the null filter).
            if user is None and require_user:
                continue
            if not include_system and user is not None and user.get("is_system"):
                continue
            if principal_type and row["principal_type"] != principal_type:
                continue
            if roles and row["role_name"] not in roles:
                continue
            if search:
                name = (user or {}).get("name") or ""
                email = (user or {}).get("email") or ""
                if search not in name.lower() and search not in email.lower():
                    continue
            out.append(row)
        return out

    @staticmethod
    def _map_base_item(row: dict[str, Any], user: dict[str, Any]) -> dict[str, Any]:
        item: dict[str, Any] = {
            "type": PRINCIPAL_USER,
            "userId": row["principal_id"],
            "userName": user.get("name"),
            "email": user.get("email"),
            "avatar": _public_avatar(user.get("avatar")),
            "role": row["role_name"],
            "createdTime": _iso(row["created_time"]),
            "lastSignTime": _iso(user.get("last_sign_time")),
            "resourceType": row["resource_type"],
            "billable": True,
        }
        if user.get("is_system"):
            item["isSystem"] = True
        return item

    async def get_list_by_base(
        self, base_id: str, options: dict[str, Any]
    ) -> list[dict[str, Any]]:
        _, rows, user_map = await self._base_tree_rows(base_id)
        visible = self._filter_base_rows(rows, user_map, options)
        # ref: orderBy collaborator.created_time desc by default.
        visible.sort(key=lambda r: r["principal_id"])
        visible.sort(key=lambda r: r["created_time"], reverse=True)
        skip = options.get("skip") or 0
        take = options.get("take") if options.get("take") is not None else 50
        paged = visible[skip : skip + take]
        return [self._map_base_item(row, user_map[row["principal_id"]]) for row in paged]

    async def get_total_base(self, base_id: str, options: dict[str, Any]) -> int:
        _, rows, user_map = await self._base_tree_rows(base_id)
        # getTotalBase leftJoins users without whereNotNull, so non-user
        # principals are counted too.
        return len(self._filter_base_rows(rows, user_map, options, require_user=False))

    async def get_user_collaborators(
        self, base_id: str, options: dict[str, Any]
    ) -> list[dict[str, Any]]:
        _, rows, user_map = await self._base_tree_rows(base_id)
        visible = self._filter_base_rows(rows, user_map, options)
        reverse = (options.get("orderBy") or "desc") == "desc"
        visible.sort(key=lambda r: r["principal_id"])
        visible.sort(key=lambda r: r["created_time"], reverse=reverse)
        skip = options.get("skip") or 0
        take = options.get("take") if options.get("take") is not None else 50
        paged = visible[skip : skip + take]
        return [
            {
                "id": user_map[row["principal_id"]]["id"],
                "name": user_map[row["principal_id"]]["name"],
                "email": user_map[row["principal_id"]]["email"],
                "avatar": _public_avatar(user_map[row["principal_id"]].get("avatar")),
                "created_time": _iso(row["created_time"]),
            }
            for row in paged
        ]

    async def add_base_collaborators(
        self, base_id: str, collaborators: list[dict[str, str]], role: str
    ) -> dict[str, int]:
        user_id = cls.get("user.id")
        await self.validate_user_add_role(user_id, role, base_id, RESOURCE_BASE)
        user_ids = [c["principalId"] for c in collaborators if c["principalType"] == "user"]
        await self._validate_collaborator_users(user_ids)
        result = await self.create_base_collaborator(
            collaborators=collaborators, base_id=base_id, role=role, created_by=user_id
        )
        await self._notify_collaborator_invite(
            user_id, user_ids, RESOURCE_BASE, base_id
        )
        return result

    async def create_base_collaborator(
        self,
        collaborators: list[dict[str, str]],
        base_id: str,
        role: str,
        created_by: str | None = None,
    ) -> dict[str, int]:
        current_user_id = created_by or cls.get("user.id")
        from ..base import repository as base_repository

        base = await base_repository.get_base_row(base_id, include_deleted=True)
        if base is None:
            raise ApiError("Base not found", HttpErrorCode.NOT_FOUND)
        pairs = {(c["principalId"], c["principalType"]) for c in collaborators}
        rows = await repository.list_collaborator_rows([base_id, base["space_id"]])
        if any((r["principal_id"], r["principal_type"]) in pairs for r in rows):
            raise ApiError(
                "Collaborator has already existed in base",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.collaborator.alreadyExistedInBase"}},
            )
        now = datetime.now(UTC).replace(tzinfo=None)
        await repository.insert_collaborators(
            [
                {
                    "id": random_string(16),
                    "resource_id": base_id,
                    "resource_type": RESOURCE_BASE,
                    "role_name": role,
                    "principal_id": c["principalId"],
                    "principal_type": c["principalType"],
                    "created_by": current_user_id,
                    "created_time": now,
                }
                for c in collaborators
            ]
        )
        return {"count": len(collaborators)}
