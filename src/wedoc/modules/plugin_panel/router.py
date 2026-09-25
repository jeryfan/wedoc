"""Routes for /api/table/:tableId/plugin-panel — ports plugin-panel.controller.ts."""

from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from ...core.security.auth import auth_guard, permissions
from ...core.security.permissions import permission_guard
from ...core.validation import read_json_body
from .schemas import (
    CreatePanelRo,
    DuplicateInstalledRo,
    DuplicatePanelRo,
    InstallRo,
    RenamePanelRo,
    UpdateLayoutRo,
    UpdateStorageRo,
)
from .service import PluginPanelService

router = APIRouter(
    prefix="/api/table/{tableId}/plugin-panel",
    dependencies=[Depends(auth_guard), Depends(permission_guard)],
)


@router.post("", status_code=201)
@permissions("table|update")
async def create_panel(tableId: str, request: Request) -> dict[str, Any]:
    ro = CreatePanelRo.zod_validate(await read_json_body(request))
    return await PluginPanelService().create_panel(tableId, ro)


@router.get("", status_code=200)
@permissions("table|read")
async def get_panels(tableId: str) -> list[dict[str, Any]]:
    return await PluginPanelService().get_panels(tableId)


@router.post("/{panel_id}/install", status_code=201)
@permissions("table|update")
async def install_plugin(tableId: str, panel_id: str, request: Request) -> dict[str, Any]:
    ro = InstallRo.zod_validate(await read_json_body(request))
    return await PluginPanelService().install(tableId, panel_id, ro)


@router.get("/{panel_id}/plugin/{plugin_install_id}", status_code=200)
@permissions("table|read")
async def get_plugin(tableId: str, panel_id: str, plugin_install_id: str) -> dict[str, Any]:
    return await PluginPanelService().get_plugin(tableId, panel_id, plugin_install_id)


@router.delete("/{panel_id}/plugin/{plugin_install_id}", status_code=200)
@permissions("table|update")
async def remove_plugin(tableId: str, panel_id: str, plugin_install_id: str) -> Response:
    await PluginPanelService().remove_plugin(tableId, panel_id, plugin_install_id)
    return Response(status_code=200)


@router.patch("/{panel_id}/plugin/{plugin_install_id}/rename", status_code=200)
@permissions("table|update")
async def rename_plugin(
    tableId: str, panel_id: str, plugin_install_id: str, request: Request
) -> dict[str, Any]:
    ro = RenamePanelRo.zod_validate(await read_json_body(request))
    return await PluginPanelService().rename_plugin(tableId, panel_id, plugin_install_id, ro)


@router.patch("/{panel_id}/plugin/{plugin_install_id}/update-storage", status_code=200)
@permissions("table|update")
async def update_plugin_storage(
    tableId: str, panel_id: str, plugin_install_id: str, request: Request
) -> dict[str, Any]:
    ro = UpdateStorageRo.zod_validate(await read_json_body(request))
    return await PluginPanelService().update_plugin_storage(
        tableId, panel_id, plugin_install_id, ro
    )


@router.post("/{panel_id}/plugin/{plugin_install_id}/duplicate", status_code=201)
@permissions("table|update")
async def duplicate_plugin(
    tableId: str, panel_id: str, plugin_install_id: str, request: Request
) -> dict[str, Any]:
    ro = DuplicateInstalledRo.zod_validate(await read_json_body(request))
    return await PluginPanelService().duplicate_plugin(tableId, panel_id, plugin_install_id, ro)


@router.patch("/{panel_id}/rename", status_code=200)
@permissions("table|update")
async def rename_panel(tableId: str, panel_id: str, request: Request) -> dict[str, Any]:
    ro = RenamePanelRo.zod_validate(await read_json_body(request))
    return await PluginPanelService().rename_panel(tableId, panel_id, ro)


@router.patch("/{panel_id}/layout", status_code=200)
@permissions("table|update")
async def update_layout(tableId: str, panel_id: str, request: Request) -> dict[str, Any]:
    ro = UpdateLayoutRo.zod_validate(await read_json_body(request))
    return await PluginPanelService().update_layout(
        tableId, panel_id, [i.model_dump() for i in ro.layout]
    )


@router.post("/{panel_id}/duplicate", status_code=201)
@permissions("table|update")
async def duplicate_panel(tableId: str, panel_id: str, request: Request) -> dict[str, Any]:
    ro = DuplicatePanelRo.zod_validate(await read_json_body(request))
    return await PluginPanelService().duplicate_panel(tableId, panel_id, ro)


@router.get("/{panel_id}", status_code=200)
@permissions("table|read")
async def get_panel(tableId: str, panel_id: str) -> dict[str, Any]:
    return await PluginPanelService().get_panel(tableId, panel_id)


@router.delete("/{panel_id}", status_code=200)
@permissions("table|update")
async def delete_panel(tableId: str, panel_id: str) -> Response:
    await PluginPanelService().delete_panel(tableId, panel_id)
    return Response(status_code=200)
