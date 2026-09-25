"""Routes for /api/table/:tableId/plugin-context-menu — ports the controller."""

from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from ...core.security.auth import auth_guard, permissions
from ...core.security.permissions import permission_guard
from ...core.validation import read_json_body
from .schemas import InstallRo, MoveRo, RenameRo, UpdateStorageRo
from .service import PluginContextMenuService

router = APIRouter(
    prefix="/api/table/{tableId}/plugin-context-menu",
    dependencies=[Depends(auth_guard), Depends(permission_guard)],
)


@router.post("/install", status_code=201)
@permissions("table|update")
async def install(tableId: str, request: Request) -> dict[str, Any]:
    ro = InstallRo.zod_validate(await read_json_body(request))
    return await PluginContextMenuService().install(tableId, ro)


@router.get("", status_code=200)
@permissions("table|read")
async def get_list(tableId: str) -> list[dict[str, Any]]:
    return await PluginContextMenuService().get_list(tableId)


@router.get("/{plugin_install_id}/storage", status_code=200)
@permissions("table|read")
async def get_storage(tableId: str, plugin_install_id: str) -> dict[str, Any]:
    return await PluginContextMenuService().get_storage(tableId, plugin_install_id)


@router.patch("/{plugin_install_id}/rename", status_code=200)
@permissions("table|update")
async def rename(tableId: str, plugin_install_id: str, request: Request) -> dict[str, Any]:
    ro = RenameRo.zod_validate(await read_json_body(request))
    return await PluginContextMenuService().rename(tableId, plugin_install_id, ro)


@router.put("/{plugin_install_id}/update-storage", status_code=200)
@permissions("table|update")
async def update_storage(tableId: str, plugin_install_id: str, request: Request) -> dict[str, Any]:
    ro = UpdateStorageRo.zod_validate(await read_json_body(request))
    return await PluginContextMenuService().update_storage(tableId, plugin_install_id, ro)


@router.put("/{plugin_install_id}/move", status_code=200)
@permissions("table|update")
async def move(tableId: str, plugin_install_id: str, request: Request) -> Response:
    ro = MoveRo.zod_validate(await read_json_body(request))
    await PluginContextMenuService().move(tableId, plugin_install_id, ro)
    return Response(status_code=200)


@router.delete("/{plugin_install_id}", status_code=200)
@permissions("table|update")
async def remove(tableId: str, plugin_install_id: str) -> Response:
    await PluginContextMenuService().delete(tableId, plugin_install_id)
    return Response(status_code=200)


@router.get("/{plugin_install_id}", status_code=200)
@permissions("table|read")
async def get(tableId: str, plugin_install_id: str) -> dict[str, Any]:
    return await PluginContextMenuService().get(tableId, plugin_install_id)
