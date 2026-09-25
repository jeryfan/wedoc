"""Routes for /api/plugin — ports plugin.controller.ts + plugin-chart.controller.ts."""

import json
from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from ...core.errors import ApiError, HttpErrorCode
from ...core.security.auth import allow_anonymous, auth_guard, permissions, resource_meta
from ...core.security.permissions import permission_guard
from ...core.validation import read_json_body
from .schemas import CreatePluginRo, PluginGetTokenRo, PluginRefreshTokenRo, UpdatePluginRo
from .service import PluginAuthService, PluginService

# chart routes are more specific — register first
chart_router = APIRouter(
    prefix="/api/plugin/chart",
    dependencies=[Depends(auth_guard), Depends(permission_guard)],
)


@chart_router.get("/{plugin_install_id}/plugin-panel/{position_id}/query", status_code=200)
@permissions("table|read")
@resource_meta("tableId", "query")
async def plugin_panel_query(
    plugin_install_id: str, position_id: str, request: Request
) -> dict[str, Any]:
    from .chart_service import PluginChartService

    params = request.query_params
    return await PluginChartService().plugin_panel_query(
        plugin_install_id, position_id, params.get("tableId", ""), params.get("cellFormat")
    )


@chart_router.get("/{plugin_install_id}/dashboard/{position_id}/query", status_code=200)
@permissions("base|read")
@resource_meta("baseId", "query")
async def dashboard_query(
    plugin_install_id: str, position_id: str, request: Request
) -> dict[str, Any]:
    from .chart_service import PluginChartService

    params = request.query_params
    return await PluginChartService().dashboard_query(
        plugin_install_id, position_id, params.get("baseId", ""), params.get("cellFormat")
    )


router = APIRouter(prefix="/api/plugin", dependencies=[Depends(auth_guard)])


@router.post("", status_code=201)
async def create_plugin(request: Request) -> dict[str, Any]:
    ro = CreatePluginRo.zod_validate(await read_json_body(request))
    return await PluginService().create_plugin(ro)


@router.get("", status_code=200)
async def get_plugins() -> list[dict[str, Any]]:
    return await PluginService().get_plugins()


@router.get("/center/list", status_code=200)
async def get_plugin_center_list(request: Request) -> list[dict[str, Any]]:
    params = request.query_params
    ids = params.getlist("ids") or None
    positions = None
    if "positions" in params:
        try:
            positions = json.loads(params["positions"])
        except json.JSONDecodeError as exc:
            raise ApiError("Validation error", HttpErrorCode.VALIDATION_ERROR) from exc
    return await PluginService().get_plugin_center_list(positions, ids)


@router.post("/{plugin_id}/regenerate-secret", status_code=201)
async def regenerate_secret(plugin_id: str) -> dict[str, Any]:
    return await PluginService().regenerate_secret(plugin_id)


@router.post("/{plugin_id}/authCode", status_code=201)
async def auth_code(plugin_id: str, request: Request) -> str:
    body = await read_json_body(request)
    base_id = body.get("baseId", "") if isinstance(body, dict) else ""
    return await PluginAuthService().auth_code(plugin_id, base_id)


@router.patch("/{plugin_id}/submit", status_code=200)
async def submit_plugin(plugin_id: str) -> Response:
    await PluginService().submit_plugin(plugin_id)
    return Response(status_code=200)


@router.patch("/{plugin_id}/unpublish", status_code=200)
async def unpublish_plugin(plugin_id: str) -> Response:
    await PluginService().unpublish_plugin(plugin_id)
    return Response(status_code=200)


@router.get("/{plugin_id}", status_code=200)
async def get_plugin(plugin_id: str) -> dict[str, Any]:
    return await PluginService().get_plugin(plugin_id)


@router.put("/{plugin_id}", status_code=200)
async def update_plugin(plugin_id: str, request: Request) -> dict[str, Any]:
    ro = UpdatePluginRo.zod_validate(await read_json_body(request))
    return await PluginService().update_plugin(plugin_id, ro)


@router.delete("/{plugin_id}", status_code=200)
async def delete_plugin(plugin_id: str) -> Response:
    await PluginService().delete_plugin(plugin_id)
    return Response(status_code=200)


# token endpoints: @Public upstream — auth strategies still run passively to
# populate the caller (developer previewing their own unpublished plugin), so
# they are modelled as allow-anonymous rather than isPublic (which skips auth).
@router.post("/{plugin_id}/token", status_code=201)
@allow_anonymous()
async def access_token(plugin_id: str, request: Request) -> dict[str, Any]:
    ro = PluginGetTokenRo.zod_validate(await read_json_body(request))
    return await PluginAuthService().token(plugin_id, ro)


@router.post("/{plugin_id}/refreshToken", status_code=201)
@allow_anonymous()
async def refresh_token(plugin_id: str, request: Request) -> dict[str, Any]:
    ro = PluginRefreshTokenRo.zod_validate(await read_json_body(request))
    return await PluginAuthService().refresh_token(plugin_id, ro)
