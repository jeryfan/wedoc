"""Routes for /api/admin/setting and /api/admin — ports the setting/admin controllers."""

from typing import Any

from fastapi import APIRouter, Depends, Request, Response, UploadFile

from ...core.errors import ApiError, HttpErrorCode
from ...core.security.auth import auth_guard, permissions
from ...core.security.permissions import permission_guard
from ...core.validation import read_json_body
from .admin_service import AdminService
from .schemas import (
    AdminSendNotificationRo,
    BatchTestLLMRo,
    SetMailTransportConfigRo,
    TestApiKeyRo,
    TestLLMRo,
    UpdateAiConfigRo,
    UpdateAppConfigRo,
    UpdateSettingRo,
)
from .service import SettingService

_LOGO_MAX_SIZE = 500 * 1024

# --- public (no auth) ------------------------------------------------------
public_router = APIRouter(prefix="/api/admin/setting")


@public_router.get("/public", status_code=200)
async def get_public_setting() -> dict[str, Any]:
    return await SettingService().get_public_setting()


# --- instance-gated setting surface ----------------------------------------
router = APIRouter(
    prefix="/api/admin/setting",
    dependencies=[Depends(auth_guard), Depends(permission_guard)],
)


@router.get("", status_code=200)
@permissions("instance|read")
async def get_setting() -> dict[str, Any]:
    return await SettingService().get_setting()


@router.patch("", status_code=200)
@permissions("instance|update")
async def update_setting(request: Request) -> dict[str, Any]:
    ro = UpdateSettingRo.zod_validate(await read_json_body(request))
    return await SettingService().update_setting(ro.model_dump(exclude_unset=True))


@router.patch("/ai-config", status_code=200)
@permissions("instance|update")
async def update_ai_config(request: Request) -> dict[str, Any]:
    ro = UpdateAiConfigRo.zod_validate(await read_json_body(request))
    return await SettingService().update_ai_config(ro)


@router.patch("/app-config", status_code=200)
@permissions("instance|update")
async def update_app_config(request: Request) -> dict[str, Any]:
    ro = UpdateAppConfigRo.zod_validate(await read_json_body(request))
    return await SettingService().update_app_config(ro)


@router.patch("/logo", status_code=200)
@permissions("instance|update")
async def upload_logo(file: UploadFile) -> dict[str, Any]:
    content_type = file.content_type or ""
    if not content_type.startswith("image/"):
        raise ApiError("Invalid file type", HttpErrorCode.VALIDATION_ERROR)
    data = await file.read()
    if len(data) > _LOGO_MAX_SIZE:
        raise ApiError("File too large", HttpErrorCode.PAYLOAD_TOO_LARGE)
    return await SettingService().upload_logo(data, content_type)


@router.post("/test-llm", status_code=201)
@permissions("instance|update")
async def test_llm(request: Request) -> dict[str, Any]:
    ro = TestLLMRo.zod_validate(await read_json_body(request))
    return await SettingService().test_llm(ro)


@router.post("/batch-test-llm", status_code=201)
@permissions("instance|update")
async def batch_test_llm(request: Request) -> dict[str, Any]:
    raw = await read_json_body(request)
    providers = None
    if isinstance(raw, dict):
        ro = BatchTestLLMRo.zod_validate(raw)
        providers = [p.model_dump() for p in ro.providers] if ro.providers else None
    return await SettingService().batch_test_llm(providers)


@router.post("/test-api-key", status_code=201)
@permissions("instance|update")
async def test_api_key(request: Request) -> dict[str, Any]:
    ro = TestApiKeyRo.zod_validate(await read_json_body(request))
    return await SettingService().test_api_key(ro)


@router.get("/test-public-access", status_code=200)
@permissions("instance|update")
async def test_public_access() -> dict[str, Any]:
    return await SettingService().test_public_access()


@router.put("/set-mail-transport-config", status_code=200)
@permissions("instance|update")
async def set_mail_transport_config(request: Request) -> dict[str, Any]:
    ro = SetMailTransportConfigRo.zod_validate(await read_json_body(request))
    return await SettingService().set_mail_transport_config(ro)


# --- admin surface ---------------------------------------------------------
admin_router = APIRouter(
    prefix="/api/admin",
    dependencies=[Depends(auth_guard), Depends(permission_guard)],
)


@admin_router.patch("/plugin/{plugin_id}/publish", status_code=200)
@permissions("instance|update")
async def publish_plugin(plugin_id: str) -> Response:
    await AdminService().publish_plugin(plugin_id)
    return Response(status_code=200)


@admin_router.patch("/plugin/{plugin_id}/unpublish", status_code=200)
@permissions("instance|update")
async def unpublish_plugin(plugin_id: str) -> Response:
    await AdminService().unpublish_plugin(plugin_id)
    return Response(status_code=200)


@admin_router.post("/attachment/repair-table-thumbnail", status_code=201)
@permissions("instance|update")
async def repair_table_attachment_thumbnail() -> Response:
    await AdminService().repair_table_attachment_thumbnail()
    return Response(status_code=201)


@admin_router.get("/debug/heap-snapshot", status_code=200)
@permissions("instance|update")
async def get_heap_snapshot() -> Response:
    # Heap snapshot streaming is deferred; return an empty octet-stream.
    return Response(content=b"", media_type="application/octet-stream")


@admin_router.get("/performance-cache-stats", status_code=200)
@permissions("instance|update")
async def get_performance_cache() -> dict[str, Any]:
    return await AdminService().get_performance_cache()


@admin_router.delete("/performance-cache", status_code=200)
@permissions("instance|update")
async def delete_performance_cache(request: Request) -> Response:
    key = request.query_params.get("key")
    await AdminService().delete_performance_cache(key)
    return Response(status_code=200)


@admin_router.post("/notification", status_code=201)
@permissions("instance|update")
async def send_notification(request: Request) -> dict[str, Any]:
    ro = AdminSendNotificationRo.zod_validate(await read_json_body(request))
    return await AdminService().send_admin_notification(ro)
