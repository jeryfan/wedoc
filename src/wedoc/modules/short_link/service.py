"""Short-link service — ports features/short-link/short-link.service.ts."""

from typing import Any

from ...core import cls
from ...core.errors import ApiError, HttpErrorCode
from . import repository
from .schemas import CreateShortLinkRo


async def _resolve_target_path(link_type: str, resource_id: str) -> str:
    if link_type == "view-share":
        if not await repository.view_share_exists(resource_id):
            raise ApiError("Share view not found", HttpErrorCode.NOT_FOUND)
        return f"/share/{resource_id}/view"
    if link_type == "base-share":
        if not await repository.base_share_exists(resource_id):
            raise ApiError("Base share not found", HttpErrorCode.NOT_FOUND)
        return f"/share/{resource_id}/base"
    if link_type == "template":
        if not await repository.template_published(resource_id):
            raise ApiError("Template not found", HttpErrorCode.NOT_FOUND)
        return f"/t/{resource_id}"
    # No external resolver is registered (EE-only artifact shares).
    raise ApiError("Unsupported short link type", HttpErrorCode.VALIDATION_ERROR)


class ShortLinkService:
    async def create_short_link(self, ro: CreateShortLinkRo) -> dict[str, Any]:
        path = await _resolve_target_path(ro.type, ro.resourceId)
        existed = await repository.find_active(ro.type, ro.resourceId)
        if existed:
            return {"code": existed, "path": path}
        user_id = cls.get("user.id")
        code = await repository.insert_short_link(ro.type, ro.resourceId, user_id)
        return {"code": code, "path": path}

    async def get_short_link(self, code: str) -> dict[str, Any]:
        short_link = await repository.find_by_code(code)
        if short_link is None:
            raise ApiError("Short link not found", HttpErrorCode.NOT_FOUND)
        path = await _resolve_target_path(short_link["type"], short_link["resourceId"])
        return {"code": short_link["code"], "path": path}
