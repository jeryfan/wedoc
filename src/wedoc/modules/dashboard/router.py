"""Routes for /api/base/:baseId/dashboard — ports dashboard.controller.ts."""

from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from ...core.security.auth import (
    AllowAnonymousType,
    allow_anonymous,
    auth_guard,
    permissions,
)
from ...core.security.permissions import permission_guard
from ...core.validation import read_json_body
from .schemas import (
    CreateDashboardRo,
    DuplicateDashboardRo,
    DuplicateInstalledPluginRo,
    InstallPluginRo,
    RenameDashboardRo,
    UpdateLayoutDashboardRo,
    UpdateStorageRo,
)
from .service import DashboardService

router = APIRouter(
    prefix="/api/base/{baseId}/dashboard",
    dependencies=[Depends(auth_guard), Depends(permission_guard)],
)


@router.get("", status_code=200)
@permissions("base|read")
@allow_anonymous(AllowAnonymousType.RESOURCE)
async def get_dashboard(baseId: str) -> list[dict[str, Any]]:
    return await DashboardService().get_dashboard(baseId)


@router.post("", status_code=201)
@permissions("base|update")
async def create_dashboard(baseId: str, request: Request) -> dict[str, Any]:
    ro = CreateDashboardRo.zod_validate(await read_json_body(request))
    return await DashboardService().create_dashboard(baseId, ro)


@router.get("/{dashboard_id}/plugin/{plugin_install_id}", status_code=200)
@permissions("base|read")
@allow_anonymous(AllowAnonymousType.RESOURCE)
async def get_plugin_install(
    baseId: str, dashboard_id: str, plugin_install_id: str
) -> dict[str, Any]:
    return await DashboardService().get_plugin_install(baseId, dashboard_id, plugin_install_id)


@router.post("/{dashboard_id}/plugin/{plugin_install_id}/duplicate", status_code=201)
@permissions("base|update")
async def duplicate_installed_plugin(
    baseId: str, dashboard_id: str, plugin_install_id: str, request: Request
) -> dict[str, Any]:
    ro = DuplicateInstalledPluginRo.zod_validate(await read_json_body(request))
    return await DashboardService().duplicate_dashboard_installed_plugin(
        baseId, dashboard_id, plugin_install_id, ro
    )


@router.delete("/{dashboard_id}/plugin/{plugin_install_id}", status_code=200)
@permissions("base|update")
async def remove_plugin(baseId: str, dashboard_id: str, plugin_install_id: str) -> Response:
    await DashboardService().remove_plugin(baseId, dashboard_id, plugin_install_id)
    return Response(status_code=200)


@router.patch("/{dashboard_id}/plugin/{plugin_install_id}/rename", status_code=200)
@permissions("base|update")
async def rename_plugin(
    baseId: str, dashboard_id: str, plugin_install_id: str, request: Request
) -> dict[str, Any]:
    ro = RenameDashboardRo.zod_validate(await read_json_body(request))
    return await DashboardService().rename_plugin(baseId, dashboard_id, plugin_install_id, ro.name)


@router.patch("/{dashboard_id}/plugin/{plugin_install_id}/update-storage", status_code=200)
@permissions("base|update")
async def update_plugin_storage(
    baseId: str, dashboard_id: str, plugin_install_id: str, request: Request
) -> dict[str, Any]:
    ro = UpdateStorageRo.zod_validate(await read_json_body(request))
    return await DashboardService().update_plugin_storage(
        baseId, dashboard_id, plugin_install_id, ro
    )


@router.post("/{dashboard_id}/plugin", status_code=201)
@permissions("base|update")
async def install_plugin(baseId: str, dashboard_id: str, request: Request) -> dict[str, Any]:
    ro = InstallPluginRo.zod_validate(await read_json_body(request))
    return await DashboardService().install_plugin(baseId, dashboard_id, ro)


@router.post("/{dashboard_id}/duplicate", status_code=201)
@permissions("base|update")
async def duplicate_dashboard(baseId: str, dashboard_id: str, request: Request) -> dict[str, Any]:
    ro = DuplicateDashboardRo.zod_validate(await read_json_body(request))
    return await DashboardService().duplicate_dashboard(baseId, dashboard_id, ro)


@router.patch("/{dashboard_id}/rename", status_code=200)
@permissions("base|update")
async def rename_dashboard(baseId: str, dashboard_id: str, request: Request) -> dict[str, Any]:
    ro = RenameDashboardRo.zod_validate(await read_json_body(request))
    return await DashboardService().rename_dashboard(baseId, dashboard_id, ro.name)


@router.patch("/{dashboard_id}/layout", status_code=200)
@permissions("base|update")
async def update_layout(baseId: str, dashboard_id: str, request: Request) -> dict[str, Any]:
    ro = UpdateLayoutDashboardRo.zod_validate(await read_json_body(request))
    return await DashboardService().update_layout(
        baseId, dashboard_id, [i.model_dump() for i in ro.layout]
    )


@router.get("/{dashboard_id}", status_code=200)
@permissions("base|read")
@allow_anonymous(AllowAnonymousType.RESOURCE)
async def get_dashboard_by_id(baseId: str, dashboard_id: str) -> dict[str, Any]:
    return await DashboardService().get_dashboard_by_id(baseId, dashboard_id)


@router.delete("/{dashboard_id}", status_code=200)
@permissions("base|update")
async def delete_dashboard(baseId: str, dashboard_id: str) -> Response:
    await DashboardService().delete_dashboard(baseId, dashboard_id)
    return Response(status_code=200)
