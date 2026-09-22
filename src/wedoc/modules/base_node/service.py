"""base-node domain service — ports features/base-node/base-node.service.ts
plus folder/base-node-folder.service.ts.

Node-level permission configuration (BaseNodePermissionGuard's permission
context) is not ported: without any base_node_permission rows the upstream
guard allows everything, which matches the single-PG deployment scope.
Table/dashboard/workflow/app resource types are deferred with the table and
dashboard modules; until then only folder resources exist and the node-list
reconciliation maintains folder rows only.
"""

from datetime import UTC, datetime
from typing import Any

from ...core import cls
from ...core.errors import ApiError, HttpErrorCode
from ...core.ids import IdPrefix, new_id
from ..space import repository as space_repository
from ..space.service import get_uniq_name
from . import repository
from .schemas import RESOURCE_FOLDER

MAX_FOLDER_DEPTH = 2


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _not_found(node_id: str) -> ApiError:
    return ApiError(
        "Node not found",
        HttpErrorCode.NOT_FOUND,
        {"localization": {"i18nKey": "httpErrors.baseNode.notFound"}},
    )


def _invalid_resource_type(resource_type: str) -> ApiError:
    return ApiError(
        f"Invalid resource type {resource_type}",
        HttpErrorCode.VALIDATION_ERROR,
        {"localization": {"i18nKey": "httpErrors.baseNode.invalidResourceType"}},
    )


class BaseNodeService:
    # -- VO assembly -----------------------------------------------------------

    @staticmethod
    def _default_url(base_id: str, resource_type: str, resource_id: str) -> str:
        if resource_type == RESOURCE_FOLDER:
            return f"/base/{base_id}"
        if resource_type == "dashboard":
            return f"/base/{base_id}/dashboard/{resource_id}"
        if resource_type == "workflow":
            return f"/base/{base_id}/automation/{resource_id}"
        if resource_type == "app":
            return f"/base/{base_id}/app/{resource_id}"
        # table without a resolved default view
        return f"/base/{base_id}/table/{resource_id}"

    async def _user_map(self, user_ids: list[str | None]) -> dict[str, dict[str, Any]]:
        ids = list({uid for uid in user_ids if uid})
        if not ids:
            return {}
        users = await space_repository.list_user_rows_by_ids(ids)
        return {
            u["id"]: {"id": u["id"], "name": u["name"], "email": u["email"], "avatar": u["avatar"]}
            for u in users
        }

    def _entry2vo(
        self,
        entry: dict[str, Any],
        children: list[dict[str, Any]],
        resource: dict[str, Any],
        user_map: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        def resolve_user(user_id: str | None) -> dict[str, Any] | None:
            if not user_id:
                return None
            return user_map.get(user_id) or None

        resource_meta: dict[str, Any] = {
            "name": resource.get("name"),
        }
        if resource.get("icon") is not None:
            resource_meta["icon"] = resource["icon"]
        if resource.get("default_view_id"):
            resource_meta["defaultViewId"] = resource["default_view_id"]
        resource_meta["createdTime"] = _iso(resource.get("created_time"))
        resource_meta["createdByUser"] = resolve_user(resource.get("created_by"))
        resource_meta["lastModifiedTime"] = _iso(resource.get("last_modified_time"))
        resource_meta["lastModifiedByUser"] = resolve_user(resource.get("last_modified_by"))
        resource_type = entry["resource_type"]
        return {
            "id": entry["id"],
            "baseId": entry["base_id"],
            "parentId": entry["parent_id"],
            "resourceId": entry["resource_id"],
            "order": entry["order"],
            "resourceType": resource_type,
            "parent": {"id": entry["parent_id"]} if entry["parent_id"] else None,
            "children": children,
            "resourceMeta": resource_meta,
            "defaultUrl": self._default_url(entry["base_id"], resource_type, entry["resource_id"]),
        }

    async def _vo(
        self,
        entry: dict[str, Any],
        resource: dict[str, Any] | None = None,
        user_map: dict[str, dict[str, Any]] | None = None,
        children_map: dict[str, list[dict[str, Any]]] | None = None,
    ) -> dict[str, Any]:
        if children_map is None:
            children_map = await repository.list_child_orders([entry["id"]])
        if resource is None:
            resource = await self._get_resource(
                entry["base_id"], entry["resource_type"], entry["resource_id"]
            )
        if user_map is None:
            user_map = await self._user_map(
                [resource.get("created_by"), resource.get("last_modified_by")]
            )
        return self._entry2vo(entry, children_map.get(entry["id"], []), resource, user_map)

    @staticmethod
    async def _get_resource(base_id: str, resource_type: str, resource_id: str) -> dict[str, Any]:
        if resource_type == RESOURCE_FOLDER:
            folders = await repository.list_folder_rows(base_id, [resource_id])
            if not folders:
                raise _not_found(resource_id)
            return folders[0]
        raise _invalid_resource_type(resource_type)

    # -- reads -------------------------------------------------------------------

    async def get_list(self, base_id: str) -> list[dict[str, Any]]:
        return await self._prepare_node_list(base_id)

    async def get_tree(self, base_id: str) -> dict[str, Any]:
        return {"nodes": await self._prepare_node_list(base_id), "maxFolderDepth": MAX_FOLDER_DEPTH}

    async def get_node_vo(self, base_id: str, node_id: str) -> dict[str, Any]:
        entry = await repository.get_node_row(node_id)
        if entry is None or entry["base_id"] != base_id:
            raise _not_found(node_id)
        return await self._vo(entry)

    async def _prepare_node_list(self, base_id: str) -> list[dict[str, Any]]:
        resources = [
            {**folder, "type": RESOURCE_FOLDER}
            for folder in await repository.list_folder_rows(base_id)
        ]
        user_map = await self._user_map(
            [r.get("created_by") for r in resources]
            + [r.get("last_modified_by") for r in resources]
        )
        resource_map = {f"{r['type']}_{r['id']}": r for r in resources}
        resource_keys = set(resource_map)

        nodes = await repository.list_node_rows(base_id)
        node_keys = {f"{n['resource_type']}_{n['resource_id']}" for n in nodes}

        to_create = [r for r in resources if f"{r['type']}_{r['id']}" not in node_keys]
        to_delete = [
            n for n in nodes if f"{n['resource_type']}_{n['resource_id']}" not in resource_keys
        ]
        valid_parent_ids = {n["id"] for n in nodes} - {n["id"] for n in to_delete}
        orphans = [
            n
            for n in nodes
            if n["parent_id"] and n["parent_id"] not in valid_parent_ids and n not in to_delete
        ]

        if not to_create and not to_delete and not orphans:
            return await self._vo_list(base_id, nodes, resource_map, user_map)

        if to_delete:
            await repository.delete_node_rows([n["id"] for n in to_delete])
        next_order = 0
        if to_create or orphans:
            next_order = int(await repository.get_max_order(base_id)) + 1
        for resource in to_create:
            await repository.insert_node(
                {
                    "id": new_id(IdPrefix.BASE_NODE),
                    "base_id": base_id,
                    "resource_type": resource["type"],
                    "resource_id": resource["id"],
                    "order": next_order,
                    "parent_id": None,
                    "created_by": cls.get("user.id"),
                }
            )
            next_order += 1
        for index, orphan in enumerate(orphans):
            await repository.update_node_row(
                orphan["id"],
                {"parent_id": None, "order": next_order + index},
            )
        nodes = await repository.list_node_rows(base_id)
        return await self._vo_list(base_id, nodes, resource_map, user_map)

    async def _vo_list(
        self,
        base_id: str,
        nodes: list[dict[str, Any]],
        resource_map: dict[str, dict[str, Any]],
        user_map: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        del base_id
        children_map = await repository.list_child_orders([n["id"] for n in nodes])
        result = []
        for entry in nodes:
            resource = resource_map[f"{entry['resource_type']}_{entry['resource_id']}"]
            result.append(
                self._entry2vo(entry, children_map.get(entry["id"], []), resource, user_map)
            )
        return result

    # -- mutations -----------------------------------------------------------------

    async def create(self, base_id: str, body: Any) -> dict[str, Any]:
        resource_type = body.resourceType
        parent_id = body.parentId
        parent_node = None
        if parent_id:
            parent_node = await self._get_parent_node(base_id, parent_id)
            if parent_node["resource_type"] != RESOURCE_FOLDER:
                raise ApiError(
                    "Parent must be a folder",
                    HttpErrorCode.VALIDATION_ERROR,
                    {"localization": {"i18nKey": "httpErrors.baseNode.parentMustBeFolder"}},
                )
        if parent_node and resource_type == RESOURCE_FOLDER:
            await self._assert_folder_depth(base_id, parent_node["id"])

        if resource_type != RESOURCE_FOLDER:
            raise _invalid_resource_type(resource_type)
        folder = await self._create_folder_resource(base_id, body.name)
        # createResource returns {id, name} only — the VO audit fields stay null.
        resource = {"id": folder["id"], "name": folder["name"]}
        # placement parents the node under the resolved parent node, not the
        # raw (possibly resource) id from the request body.
        placement_parent = parent_node["id"] if parent_node else None
        max_order = await repository.get_max_order(base_id)
        entry = await repository.insert_node(
            {
                "id": new_id(IdPrefix.BASE_NODE),
                "base_id": base_id,
                "resource_type": resource_type,
                "resource_id": resource["id"],
                "order": max_order + 1,
                "parent_id": placement_parent,
                "created_by": cls.get("user.id"),
            }
        )
        return await self._vo(entry, resource)

    async def _create_folder_resource(self, base_id: str, name: str) -> dict[str, Any]:
        names = await repository.list_folder_names(base_id)
        folder = await repository.insert_folder(
            {
                "id": new_id(IdPrefix.BASE_NODE_FOLDER),
                "base_id": base_id,
                "name": get_uniq_name(name, names),
                "created_by": cls.get("user.id"),
                # prisma @updatedAt also stamps the row on INSERT.
                "last_modified_time": datetime.now(UTC).replace(tzinfo=None),
            }
        )
        return folder

    async def duplicate(self, base_id: str, node_id: str, body: Any) -> dict[str, Any]:
        anchor = await repository.get_node_row(node_id)
        if anchor is None or anchor["base_id"] != base_id:
            raise _not_found(node_id)
        if anchor["resource_type"] == RESOURCE_FOLDER:
            raise ApiError(
                "Cannot duplicate folder",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.baseNode.cannotDuplicateFolder"}},
            )
        raise _invalid_resource_type(anchor["resource_type"])

    async def update(self, base_id: str, node_id: str, body: Any) -> dict[str, Any]:
        entry = await repository.get_node_row(node_id)
        if entry is None or entry["base_id"] != base_id:
            raise _not_found(node_id)
        resource_type = entry["resource_type"]
        if resource_type == RESOURCE_FOLDER:
            if body.name:
                await rename_folder(base_id, entry["resource_id"], body.name)
        else:
            raise _invalid_resource_type(resource_type)
        return await self._vo(entry)

    async def move(self, base_id: str, node_id: str, body: Any) -> dict[str, Any]:
        node = await repository.get_node_row(node_id)
        if node is None or node["base_id"] != base_id:
            raise _not_found(node_id)
        parent_id = body.parentId
        anchor_id = body.anchorId
        parent_set = "parentId" in body.model_fields_set
        if isinstance(parent_id, str) and isinstance(anchor_id, str):
            raise ApiError(
                "Only one of parentId or anchorId must be provided",
                HttpErrorCode.VALIDATION_ERROR,
                {
                    "localization": {
                        "i18nKey": "httpErrors.baseNode.onlyOneOfParentIdOrAnchorIdRequired"
                    }
                },
            )
        if parent_id == node_id:
            raise ApiError(
                "Cannot move node to itself",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.baseNode.cannotMoveToItself"}},
            )
        if anchor_id == node_id:
            raise ApiError(
                "Cannot move node to its own child (circular reference)",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.baseNode.cannotMoveToCircularReference"}},
            )

        if anchor_id:
            new_node = await self._move_node_to(base_id, node, anchor_id, body.position)
        elif parent_set and parent_id is None:
            max_order = await repository.get_max_order(base_id)
            new_node = await repository.update_node_row(
                node_id,
                {
                    "parent_id": None,
                    "order": max_order + 1,
                    "last_modified_by": cls.get("user.id"),
                },
            )
        elif parent_id:
            new_node = await self._move_node_to_folder(base_id, node, parent_id)
        else:
            raise ApiError(
                "At least one of parentId or anchorId must be provided",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.baseNode.anchorIdOrParentIdRequired"}},
            )
        assert new_node is not None
        return await self._vo(new_node)

    async def _move_node_to(
        self, base_id: str, node: dict[str, Any], anchor_id: str, position: str | None
    ) -> dict[str, Any]:
        anchor = await repository.get_node_row(anchor_id)
        if anchor is None or anchor["base_id"] != base_id:
            raise ApiError(
                f"Anchor {anchor_id} not found",
                HttpErrorCode.NOT_FOUND,
                {"localization": {"i18nKey": "httpErrors.baseNode.anchorNotFound"}},
            )
        if node["resource_type"] == RESOURCE_FOLDER and anchor["parent_id"]:
            await self._assert_folder_move_depth(base_id, anchor["parent_id"], node["id"])
        new_order = await self._compute_order(base_id, node, anchor, position or "after")
        updated = await repository.update_node_row(
            node["id"], {"parent_id": anchor["parent_id"], "order": new_order}
        )
        assert updated is not None
        return updated

    async def _compute_order(
        self, base_id: str, node: dict[str, Any], anchor: dict[str, Any], position: str
    ) -> float:
        before = position == "before"
        neighbor = await self._next_node(base_id, anchor, below=before, exclude_id=None)
        if neighbor is None:
            return anchor["order"] + (-1 if before else 1)
        order = (neighbor["order"] + anchor["order"]) / 2
        if abs(order - anchor["order"]) < 2 * 2.220446049250313e-16:
            await self._shuffle_orders(base_id, anchor["parent_id"])
            anchor = await repository.get_node_row(anchor["id"])
            assert anchor is not None
            return await self._compute_order(base_id, node, anchor, position)
        return order

    async def _next_node(
        self, base_id: str, anchor: dict[str, Any], *, below: bool, exclude_id: str | None
    ) -> dict[str, Any] | None:
        rows = await repository.list_node_rows(base_id)
        candidates = [
            n
            for n in rows
            if n["parent_id"] == anchor["parent_id"]
            and n["id"] != anchor["id"]
            and (exclude_id is None or n["id"] != exclude_id)
            and (n["order"] < anchor["order"] if below else n["order"] > anchor["order"])
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda n: n["order"], reverse=below)
        return candidates[0]

    async def _shuffle_orders(self, base_id: str, parent_id: str | None) -> None:
        rows = await repository.list_node_rows(base_id)
        siblings = sorted(
            [n for n in rows if n["parent_id"] == parent_id], key=lambda n: n["order"]
        )
        for index, sibling in enumerate(siblings):
            await repository.update_node_row(
                sibling["id"],
                {"order": float(index + 10), "last_modified_by": cls.get("user.id")},
            )

    async def _move_node_to_folder(
        self, base_id: str, node: dict[str, Any], parent_id: str
    ) -> dict[str, Any]:
        parent = await repository.get_node_row(parent_id)
        if parent is None or parent["base_id"] != base_id:
            raise ApiError(
                f"Parent {parent_id} not found",
                HttpErrorCode.NOT_FOUND,
                {"localization": {"i18nKey": "httpErrors.baseNode.parentNotFound"}},
            )
        if parent["resource_type"] != RESOURCE_FOLDER:
            raise ApiError(
                f"Parent {parent_id} is not a folder",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.baseNode.parentIsNotFolder"}},
            )
        if node["resource_type"] == RESOURCE_FOLDER:
            await self._assert_folder_move_depth(base_id, parent_id, node["id"])
        max_order = await repository.get_max_order(base_id)
        updated = await repository.update_node_row(
            node["id"],
            {
                "parent_id": parent_id,
                "order": max_order + 1,
                "last_modified_by": cls.get("user.id"),
            },
        )
        assert updated is not None
        return updated

    async def delete(self, base_id: str, node_id: str, permanent: bool = False) -> dict[str, Any]:
        node = await repository.get_node_row(node_id)
        if node is None or node["base_id"] != base_id:
            raise _not_found(node_id)
        if node["resource_type"] == RESOURCE_FOLDER:
            children = [
                n for n in await repository.list_node_rows(base_id) if n["parent_id"] == node_id
            ]
            if children:
                raise ApiError(
                    "Cannot delete folder because it is not empty",
                    HttpErrorCode.VALIDATION_ERROR,
                    {"localization": {"i18nKey": "httpErrors.baseNode.cannotDeleteEmptyFolder"}},
                )
        if node["resource_type"] != RESOURCE_FOLDER:
            raise _invalid_resource_type(node["resource_type"])
        await repository.delete_folder_row(base_id, node["resource_id"])
        await repository.delete_node_row(node_id)
        return {"resourceType": node["resource_type"], "resourceId": node["resource_id"]}

    # -- folder depth ------------------------------------------------------------

    async def _get_parent_node(self, base_id: str, node_id: str) -> dict[str, Any]:
        from sqlalchemy import or_, select

        from ...db import engine as db_engine
        from ...db.models_meta import BaseNode as BaseNodeModel

        async with db_engine.session() as session:
            row = (
                (
                    await session.execute(
                        select(BaseNodeModel).where(
                            BaseNodeModel.base_id == base_id,
                            or_(
                                BaseNodeModel.id == node_id,
                                BaseNodeModel.resource_id == node_id,
                            ),
                        )
                    )
                )
                .scalars()
                .first()
            )
        if row is None:
            raise ApiError(
                "Base node not found",
                HttpErrorCode.NOT_FOUND,
                {"localization": {"i18nKey": "httpErrors.baseNode.notFound"}},
            )
        return {c.name: getattr(row, c.name) for c in BaseNodeModel.__table__.columns}

    async def _assert_folder_depth(self, base_id: str, folder_id: str) -> None:
        if await self._folder_depth(base_id, folder_id) >= MAX_FOLDER_DEPTH:
            raise ApiError(
                "Folder depth limit exceeded",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.baseNode.folderDepthLimitExceeded"}},
            )

    async def _assert_folder_move_depth(self, base_id: str, parent_id: str, node_id: str) -> None:
        all_folders = await repository.list_folder_node_rows(base_id)
        parent_depth = _folder_depth_from_list(all_folders, parent_id, node_id)
        subtree_depth = _folder_subtree_depth(all_folders, node_id)
        if parent_depth + subtree_depth >= MAX_FOLDER_DEPTH:
            raise ApiError(
                "Folder depth limit exceeded",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.baseNode.folderDepthLimitExceeded"}},
            )

    async def _folder_depth(self, base_id: str, folder_id: str) -> int:
        return _folder_depth_from_list(await repository.list_folder_node_rows(base_id), folder_id)


def _folder_depth_from_list(
    all_folders: list[dict[str, Any]], folder_id: str, circular_check_node_id: str | None = None
) -> int:
    folder_map = {f["id"]: f for f in all_folders}
    visited: set[str] = set()
    depth = 0
    current: str | None = folder_id
    while current:
        if (circular_check_node_id and current == circular_check_node_id) or current in visited:
            raise ApiError(
                "Circular reference detected in folder hierarchy",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.baseNode.circularReference"}},
            )
        visited.add(current)
        depth += 1
        folder = folder_map.get(current)
        if folder is None:
            raise ApiError(
                "Folder not found",
                HttpErrorCode.NOT_FOUND,
                {"localization": {"i18nKey": "httpErrors.baseNode.folderNotFound"}},
            )
        current = folder["parent_id"]
    return depth


def _folder_subtree_depth(all_folders: list[dict[str, Any]], node_id: str) -> int:
    children_map: dict[str, list[str]] = {}
    for folder in all_folders:
        if folder["parent_id"]:
            children_map.setdefault(folder["parent_id"], []).append(folder["id"])

    def calc(fid: str) -> int:
        children = children_map.get(fid)
        if not children:
            return 0
        return 1 + max(calc(c) for c in children)

    return calc(node_id)


# ---- folder endpoints (node/folder) -------------------------------------------


async def create_folder(base_id: str, name: str) -> dict[str, Any]:
    names = await repository.list_folder_names(base_id)
    folder = await repository.insert_folder(
        {
            "id": new_id(IdPrefix.BASE_NODE_FOLDER),
            "base_id": base_id,
            "name": get_uniq_name(name, names),
            "created_by": cls.get("user.id"),
            # prisma @updatedAt also stamps the row on INSERT.
            "last_modified_time": datetime.now(UTC).replace(tzinfo=None),
        }
    )
    return {"id": folder["id"], "name": folder["name"]}


async def rename_folder(base_id: str, folder_id: str, name: str) -> dict[str, Any]:
    existing = await repository.find_folder_by_name(base_id, name, exclude_id=folder_id)
    if existing:
        raise ApiError(
            "Folder name already exists",
            HttpErrorCode.VALIDATION_ERROR,
            {"localization": {"i18nKey": "httpErrors.baseNode.nameAlreadyExists"}},
        )
    folder = await repository.update_folder_row(
        folder_id, {"name": name, "last_modified_by": cls.get("user.id")}
    )
    if folder is None:
        raise ApiError(
            "Folder not found",
            HttpErrorCode.NOT_FOUND,
            {"localization": {"i18nKey": "httpErrors.baseNode.folderNotFound"}},
        )
    return {"id": folder["id"], "name": folder["name"]}


async def delete_folder(base_id: str, folder_id: str) -> dict[str, Any] | None:
    """The folder row only; the node row is reclaimed by the next node-list
    reconciliation (upstream deletes the node lazily the same way)."""
    return await repository.delete_folder_row(base_id, folder_id)
