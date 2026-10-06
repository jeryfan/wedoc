"""base-share domain service — ports base-share.service.ts + base-share-auth.service.ts.

copyBaseShare (M5) copies a shared base into a target space via the base
duplicate engine (whole-base share). Fine-grained short-link cleanup and audit
emission are not ported (no observable effect on these REST responses).
"""

from typing import Any

from ...core import cls
from ...core.errors import ApiError, HttpErrorCode
from ...core.ids import IdPrefix, new_id
from ...core.security.auth import JwtService
from . import repository

_NOT_FOUND_KEY = "httpErrors.baseShare.notFound"
_ALREADY_EXISTS_KEY = "httpErrors.baseShare.alreadyExists"


def _share_not_found() -> ApiError:
    return ApiError(
        "Base share not found",
        HttpErrorCode.NOT_FOUND,
        {"localization": {"i18nKey": _NOT_FOUND_KEY}},
    )


def _format_vo(share: dict[str, Any]) -> dict[str, Any]:
    return {
        "baseId": share["baseId"],
        "shareId": share["shareId"],
        "password": share["password"] is not None,
        "nodeId": share["nodeId"],
        "allowSave": share["allowSave"],
        "allowCopy": share["allowCopy"],
        "allowEdit": share["allowEdit"],
        "enabled": share["enabled"],
    }


def _resolve_edit_save(
    allow_edit: bool | None, allow_save: bool | None
) -> tuple[bool | None, bool | None]:
    edit = allow_edit if allow_edit is not None else None
    save = allow_save if allow_save is not None else None
    if edit:
        return True, False
    if save:
        return False, True
    return edit, save


class BaseShareService:
    # -- management ------------------------------------------------------------
    async def create_base_share(self, base_id: str, node_id: str | None) -> dict[str, Any]:
        existing = await repository.find_by_base_node(base_id, node_id)
        if existing:
            if not existing["enabled"]:
                await repository.delete_by_id(existing["id"])
            else:
                raise ApiError(
                    "A share already exists for this node",
                    HttpErrorCode.CONFLICT,
                    {"localization": {"i18nKey": _ALREADY_EXISTS_KEY}},
                )
        share = await repository.create(
            base_id, new_id(IdPrefix.SHARE), node_id, cls.get("user.id")
        )
        return _format_vo(share)

    async def get_base_share_list(self, base_id: str) -> list[dict[str, Any]]:
        return await repository.list_enabled(base_id)

    async def get_base_share(self, base_id: str) -> dict[str, Any] | None:
        share = await repository.find_enabled_by_base_node(base_id, None)
        return _format_vo(share) if share else None

    async def get_base_share_by_node_id(
        self, base_id: str, node_id: str
    ) -> dict[str, Any] | None:
        share = await repository.find_enabled_by_base_node(base_id, node_id)
        return _format_vo(share) if share else None

    async def update_base_share(
        self, base_id: str, share_id: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        share = await repository.find_enabled_by_base_share(base_id, share_id)
        if not share:
            raise _share_not_found()

        if data.get("allowEdit") and share["nodeId"]:
            if not await repository.is_editable_node(share["nodeId"]):
                raise ApiError(
                    "allowEdit is only supported for table or folder nodes",
                    HttpErrorCode.VALIDATION_ERROR,
                )

        allow_edit_in = data["allowEdit"] if "allowEdit" in data else share["allowEdit"]
        allow_save_in = data["allowSave"] if "allowSave" in data else share["allowSave"]
        allow_edit, allow_save = _resolve_edit_save(allow_edit_in, allow_save_in)

        values = {
            "password": data["password"] if "password" in data else share["password"],
            "allow_save": allow_save,
            "allow_copy": data["allowCopy"] if "allowCopy" in data else share["allowCopy"],
            "allow_edit": allow_edit,
            "enabled": data["enabled"] if "enabled" in data else share["enabled"],
        }
        updated = await repository.update_by_id(share["id"], values)
        return _format_vo(updated)

    async def delete_base_share(self, base_id: str, share_id: str) -> None:
        share = await repository.find_enabled_by_base_share(base_id, share_id)
        if not share:
            raise _share_not_found()
        await repository.update_by_id(share["id"], {"enabled": False})

    async def refresh_base_share_id(
        self, base_id: str, share_id: str
    ) -> dict[str, Any]:
        share = await repository.find_enabled_by_base_share(base_id, share_id)
        if not share:
            raise _share_not_found()
        updated = await repository.update_by_id(
            share["id"], {"share_id": new_id(IdPrefix.SHARE)}
        )
        return _format_vo(updated)

    # -- auth (public) ---------------------------------------------------------
    async def auth_base_share(self, share_id: str, password: str | None) -> str | None:
        share = await repository.find_by_share_id(share_id)
        if not share or not share["enabled"]:
            return None
        if not share["password"]:
            raise ApiError(
                "Password restriction is not enabled",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.shareAuth.passwordRestrictionNotEnabled"}},
            )
        return share_id if password == share["password"] else None

    def auth_token(self, share_id: str, password: str) -> str:
        return JwtService().sign({"shareId": share_id, "password": password}, expires_in="7d")

    async def get_base_share_info(self, share_id: str) -> dict[str, Any]:
        share = await repository.find_by_share_id(share_id)
        if not share or not share["enabled"]:
            raise ApiError(
                "Project share not found",
                HttpErrorCode.NOT_FOUND,
                {"localization": {"i18nKey": _NOT_FOUND_KEY}},
            )
        return {
            "shareId": share["shareId"],
            "baseId": share["baseId"],
            "nodeId": share["nodeId"],
            "allowSave": share["allowSave"],
            "allowCopy": share["allowCopy"],
            "allowEdit": share["allowEdit"],
        }

    async def has_password(self, share_id: str) -> bool:
        share = await repository.find_by_share_id(share_id)
        if not share or not share["enabled"]:
            return False
        return bool(share["password"])

    async def copy_base_share(
        self,
        share_id: str,
        jwt_cookie: str | None,
        space_id: str,
        name: str | None,
        with_records: bool,
        target_base_id: str | None,
    ) -> dict[str, Any]:
        info = await self.get_base_share_info(share_id)
        if await self.has_password(share_id):
            if not jwt_cookie:
                raise ApiError("Unauthorized", HttpErrorCode.UNAUTHORIZED_SHARE)
            try:
                payload = JwtService().verify(jwt_cookie)
            except Exception as exc:
                raise ApiError("Unauthorized", HttpErrorCode.UNAUTHORIZED_SHARE) from exc
            if not await self.auth_base_share(payload.get("shareId"), payload.get("password")):
                raise ApiError("Unauthorized", HttpErrorCode.UNAUTHORIZED_SHARE)
        if not info["allowSave"]:
            raise ApiError(
                "This share does not allow copying",
                HttpErrorCode.RESTRICTED_RESOURCE,
                {"localization": {"i18nKey": "httpErrors.baseShare.copyNotAllowed"}},
            )
        # Whole-base share copy. Node-scoped partial copy (single table/folder)
        # is a documented simplification: the whole base is duplicated.
        from ..base.service import BaseService

        base = await BaseService().duplicate_base_impl(
            info["baseId"], space_id, with_records, name
        )
        return {"id": base["id"], "name": base["name"], "spaceId": base["spaceId"]}

    async def get_base_share_open(self, share_id: str, jwt_cookie: str | None) -> dict[str, Any]:
        info = await self.get_base_share_info(share_id)
        if await self.has_password(share_id):
            if not jwt_cookie:
                raise ApiError("Unauthorized", HttpErrorCode.UNAUTHORIZED_SHARE)
            try:
                payload = JwtService().verify(jwt_cookie)
            except Exception as exc:
                raise ApiError("Unauthorized", HttpErrorCode.UNAUTHORIZED_SHARE) from exc
            if not await self.auth_base_share(payload.get("shareId"), payload.get("password")):
                raise ApiError("Unauthorized", HttpErrorCode.UNAUTHORIZED_SHARE)

        base_id = info["baseId"]
        node_id = info["nodeId"]
        default_url = await self._build_default_url(base_id, node_id)
        result: dict[str, Any] = {
            "baseId": base_id,
            "shareMeta": {
                "password": await self.has_password(share_id),
                "nodeId": node_id,
                "allowSave": info["allowSave"],
                "allowCopy": info["allowCopy"],
                "allowEdit": info["allowEdit"],
            },
        }
        if default_url is not None:
            result["defaultUrl"] = default_url
        return result

    async def _build_default_url(
        self, base_id: str, node_id: str | None
    ) -> str | None:
        nodes = await repository.list_base_nodes(base_id)
        if not nodes:
            if node_id is not None:
                return None
            table_id = await repository.first_table_id(base_id)
            if not table_id:
                return None
            view_id = await repository.first_view_id(table_id)
            return (
                f"/base/{base_id}/table/{table_id}/{view_id}"
                if view_id
                else f"/base/{base_id}/table/{table_id}"
            )

        target: dict[str, Any] | None = None
        if node_id is None:
            target = self._first_accessible(nodes, None)
        else:
            shared = next((n for n in nodes if n["id"] == node_id), None)
            if shared:
                if shared["resourceType"].lower() == "folder":
                    target = self._first_accessible(nodes, node_id)
                else:
                    target = {
                        "resourceType": shared["resourceType"],
                        "resourceId": shared["resourceId"],
                    }
        if not target:
            return None
        resource_type = target["resourceType"].lower()
        resource_id = target["resourceId"]
        if resource_type == "table":
            view_id = await repository.first_view_id(resource_id)
            return (
                f"/base/{base_id}/table/{resource_id}/{view_id}"
                if view_id
                else f"/base/{base_id}/table/{resource_id}"
            )
        if resource_type == "dashboard":
            return f"/base/{base_id}/dashboard/{resource_id}"
        if resource_type == "workflow":
            return f"/base/{base_id}/automation/{resource_id}"
        if resource_type == "app":
            return f"/base/{base_id}/app/{resource_id}"
        return None

    def _first_accessible(
        self, nodes: list[dict[str, Any]], parent_node_id: str | None
    ) -> dict[str, Any] | None:
        children = sorted(
            (n for n in nodes if n["parentId"] == parent_node_id),
            key=lambda n: n["order"],
        )
        for child in children:
            if child["resourceType"].lower() != "folder":
                return {
                    "resourceType": child["resourceType"],
                    "resourceId": child["resourceId"],
                }
            found = self._first_accessible(nodes, child["id"])
            if found:
                return found
        return None
