"""Routes for /api/v2 — the wired orpc endpoints + openapi.json + docs.

Responses use the orpc envelope: success -> {ok:true, data, ...}; failure ->
{ok:false, error} with the mapped HTTP status. Input validation errors mirror
the orpc/zod message shape "Input validation failed: <path>: <message>".
"""

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ...core.security.auth import auth_guard, permissions, resource_meta
from ...core.security.permissions import permission_guard
from ...core.validation import read_json_body
from .openapi import build_docs_html, build_openapi_spec
from .service import V2Error, V2Service

# --- public: spec + docs ---------------------------------------------------
public_router = APIRouter(prefix="/api/v2")


@public_router.get("/openapi.json")
async def openapi_json() -> JSONResponse:
    return JSONResponse(build_openapi_spec())


@public_router.get("/docs")
async def docs() -> HTMLResponse:
    return HTMLResponse(build_docs_html())


# --- orpc endpoints --------------------------------------------------------
router = APIRouter(
    prefix="/api/v2/tables",
    dependencies=[Depends(auth_guard), Depends(permission_guard)],
)


def _validation_error(issues: list[tuple[str, str]]) -> JSONResponse:
    body = "; ".join(f"{path}: {msg}" for path, msg in issues)
    return JSONResponse(
        {"ok": False, "error": f"Input validation failed: {body}"}, status_code=400
    )


def _require_string(payload: Any, key: str) -> tuple[str, str] | None:
    if not isinstance(payload, dict) or not isinstance(payload.get(key), str):
        return (key, "Invalid input: expected string, received undefined")
    return None


def _ok(data: Any, status: int = 200) -> JSONResponse:
    return JSONResponse({"ok": True, "data": data}, status_code=status)


def _error(exc: V2Error) -> JSONResponse:
    return JSONResponse({"ok": False, "error": exc.message}, status_code=exc.status)


@router.post("/create")
async def create_table(request: Request) -> JSONResponse:
    payload = await read_json_body(request)
    issues = [
        i
        for i in (_require_string(payload, "baseId"), _require_string(payload, "name"))
        if i
    ]
    if issues:
        return _validation_error(issues)
    try:
        return _ok(await V2Service().create_table(payload), status=201)
    except V2Error as exc:
        return _error(exc)


@router.get("/get")
async def get_table(request: Request) -> JSONResponse:
    payload = dict(request.query_params)
    issues = [
        i for i in (_require_string(payload, "baseId"), _require_string(payload, "tableId")) if i
    ]
    if issues:
        return _validation_error(issues)
    try:
        return _ok(await V2Service().get_table(payload))
    except V2Error as exc:
        return _error(exc)


@router.get("/getComputeActivity")
@permissions("table|read")
@resource_meta("tableId", "query")
async def get_compute_activity(request: Request) -> JSONResponse:
    payload = dict(request.query_params)
    issues = [
        i for i in (_require_string(payload, "baseId"), _require_string(payload, "tableId")) if i
    ]
    if issues:
        return _validation_error(issues)
    try:
        return _ok(await V2Service().get_compute_activity(payload))
    except V2Error as exc:
        return _error(exc)


@router.delete("/deleteRecords")
async def delete_records(request: Request) -> JSONResponse:
    payload = await read_json_body(request)
    issues: list[tuple[str, str]] = []
    tid = _require_string(payload, "tableId")
    if tid:
        issues.append(tid)
    record_ids = payload.get("recordIds") if isinstance(payload, dict) else None
    if not isinstance(record_ids, list):
        issues.append(("recordIds", "Invalid input: expected array, received undefined"))
    elif len(record_ids) < 1:
        issues.append(("recordIds", "At least one recordId is required"))
    if issues:
        return _validation_error(issues)
    try:
        return _ok(await V2Service().delete_records(payload))
    except V2Error as exc:
        return _error(exc)


@router.post("/updateRecords")
async def update_records(request: Request) -> JSONResponse:
    payload = await read_json_body(request)
    issues: list[tuple[str, str]] = []
    tid = _require_string(payload, "tableId")
    if tid:
        issues.append(tid)
    ft = payload.get("fieldKeyType") if isinstance(payload, dict) else None
    if ft is not None and ft not in ("id", "name", "dbFieldName"):
        issues.append(
            ("fieldKeyType", 'Invalid option: expected one of "id"|"name"|"dbFieldName"')
        )
    if issues:
        return _validation_error(issues)
    has_records = payload.get("records") is not None
    has_filter = payload.get("filter") is not None
    has_record_ids = payload.get("recordIds") is not None
    if not has_records and not has_filter and not has_record_ids:
        return _validation_error(
            [("filter", "Either records, filter, or recordIds is required")]
        )
    try:
        return _ok(await V2Service().update_records(payload))
    except V2Error as exc:
        return _error(exc)
