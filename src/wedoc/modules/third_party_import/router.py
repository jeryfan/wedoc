"""Routes for airtable / google-sheet import (prefix /api/base).

Ports airtable-import.controller.ts + google-sheet-import.controller.ts.
"""

import json
import os
from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from ...core import cls
from ...core.errors import ApiError, HttpErrorCode
from ...core.security.auth import auth_guard, token_access
from ...core.security.permissions import PermissionService, permission_guard
from ...core.validation import read_json_body
from . import service
from .schemas import (
    AirtableAnalyzeRo,
    AirtableImportRo,
    GoogleSheetAnalyzeRo,
    GoogleSheetImportRo,
    refine_airtable_credentials,
    refine_airtable_import,
    refine_google_import,
)

router = APIRouter(
    prefix="/api/base",
    dependencies=[Depends(auth_guard), Depends(permission_guard)],
)

_SSE_HEADERS = {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _sse(events: list[dict[str, Any]]) -> Response:
    body = "".join(
        f"data: {json.dumps(e, ensure_ascii=False, separators=(',', ':'))}\n\n"
        for e in events
    )
    return Response(content=body, media_type="text/event-stream", headers=_SSE_HEADERS)


async def _assert_write_target(target_resource_id: str, has_base: bool) -> None:
    perms = ["base|table_import"] if has_base else ["base|create"]
    await PermissionService().valid_permissions(
        target_resource_id, perms, cls.get("accessTokenId")
    )


@router.get("/import-google-sheet/picker-config", status_code=200)
async def google_picker_config() -> dict[str, Any]:
    api_key = os.environ.get("GOOGLE_SHEET_PICKER_API_KEY")
    client_id = os.environ.get("BACKEND_GOOGLE_CLIENT_ID")
    if not api_key or not client_id:
        raise ApiError(
            "Google Sheets import is not configured on this instance",
            HttpErrorCode.NOT_FOUND,
        )
    app_id = client_id.split("-")[0]
    if not app_id.isdigit():
        raise ApiError(
            "BACKEND_GOOGLE_CLIENT_ID does not look like a Google client id",
            HttpErrorCode.NOT_FOUND,
        )
    return {"apiKey": api_key, "appId": app_id}


@router.post("/import-airtable/analyze", status_code=201)
@token_access()
async def airtable_analyze(request: Request) -> dict[str, Any]:
    ro = AirtableAnalyzeRo.zod_validate(await read_json_body(request))
    refine_airtable_credentials(ro)
    try:
        return await service.airtable_analyze(ro.accessToken, ro.airtableBaseId)
    except service.VendorApiError as exc:
        raise ApiError(
            service.format_airtable_error(exc), HttpErrorCode.VALIDATION_ERROR
        ) from exc


@router.post("/import-airtable/stream", status_code=200)
@token_access()
async def airtable_import_stream(request: Request) -> Response:
    ro = AirtableImportRo.zod_validate(await read_json_body(request))
    # zod superRefines run during body validation, before the controller body.
    refine_airtable_import(ro)
    target = ro.baseId or ro.spaceId
    if not target:
        raise ApiError("Either baseId or spaceId is required.", HttpErrorCode.VALIDATION_ERROR)
    await _assert_write_target(target, bool(ro.baseId))
    events: list[dict[str, Any]] = []
    try:
        await service.airtable_analyze(ro.accessToken, ro.airtableBaseId)
        events.append({"type": "error", "message": service._DEFERRED})
    except service.VendorApiError as exc:
        events.append({"type": "error", "message": service.format_airtable_error(exc)})
    except ApiError as exc:
        events.append({"type": "error", "message": exc.message})
    return _sse(events)


@router.post("/import-google-sheet/analyze", status_code=201)
@token_access()
async def google_analyze(request: Request) -> dict[str, Any]:
    ro = GoogleSheetAnalyzeRo.zod_validate(await read_json_body(request))
    try:
        return await service.google_analyze(ro.accessToken, ro.spreadsheetId)
    except service.VendorApiError as exc:
        raise ApiError(
            service.format_google_error(exc), HttpErrorCode.VALIDATION_ERROR
        ) from exc


@router.post("/import-google-sheet/stream", status_code=200)
@token_access()
async def google_import_stream(request: Request) -> Response:
    ro = GoogleSheetImportRo.zod_validate(await read_json_body(request))
    refine_google_import(ro)
    target = ro.baseId or ro.spaceId
    if not target:
        raise ApiError("Either baseId or spaceId is required.", HttpErrorCode.VALIDATION_ERROR)
    await _assert_write_target(target, bool(ro.baseId))
    events: list[dict[str, Any]] = []
    try:
        await service.google_analyze(ro.accessToken, ro.spreadsheetId)
        events.append({"type": "error", "message": service._DEFERRED})
    except service.VendorApiError as exc:
        events.append({"type": "error", "message": service.format_google_error(exc)})
    except ApiError as exc:
        events.append({"type": "error", "message": exc.message})
    return _sse(events)
