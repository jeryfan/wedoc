"""Routes for /api/table/:tableId/view.

Ports view-open-api.controller.ts. The socket snapshot/doc-ids endpoints
(M3 realtime) are implemented here; filter-link-records waits for link fields;
the plugin view endpoints need plugin infrastructure (deferred, see
docs/api-parity-ledger.md).
"""

from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from ...core.errors import ApiError, HttpErrorCode
from ...core.query import query_list
from ...core.security.auth import allow_anonymous, auth_guard, permissions
from ...core.security.permissions import permission_guard
from ...core.validation import read_json_body
from .plugin_service import ViewPluginService
from .schemas import (
    ColumnMetaItem,
    ManualSortBody,
    RecordOrderBody,
    ShareMetaBody,
    ViewCreateBody,
    ViewDescriptionBody,
    ViewFilterBody,
    ViewGroupBody,
    ViewLockedBody,
    ViewNameBody,
    ViewOptionsBody,
    ViewOrderBody,
    ViewSortBody,
)
from .service import ViewService

router = APIRouter(
    prefix="/api/table/{tableId}/view",
    dependencies=[Depends(auth_guard), Depends(permission_guard)],
)


def _js_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def _raise_zod(issues: list[tuple[str, str]]) -> None:
    if issues:
        body = "; ".join(f'{msg} at "{path}"' for msg, path in issues)
        raise ApiError("Validation error: " + body, HttpErrorCode.VALIDATION_ERROR)


def _sort_item_issues(item: Any, path: str) -> list[tuple[str, str]]:
    # sortItemSchema = { fieldId: z.string(), order: z.enum(["asc","desc"]) }; a
    # missing/invalid enum reports "Invalid option" (zod nativeEnum), extra keys pass.
    if not isinstance(item, dict):
        return [(f"Invalid input: expected object, received {_js_type(item)}", path)]
    issues: list[tuple[str, str]] = []
    if not isinstance(item.get("fieldId"), str):
        received = "undefined" if "fieldId" not in item else _js_type(item.get("fieldId"))
        issues.append((f"Invalid input: expected string, received {received}", f"{path}.fieldId"))
    if item.get("order") not in ("asc", "desc"):
        issues.append(('Invalid option: expected one of "asc"|"desc"', f"{path}.order"))
    return issues


def _validate_sort(sort: Any) -> None:
    # sortSchema = z.object({ sortObjs: sortItemSchema.array(), ... }).nullable():
    # a null value clears the sort, so only a present object body is checked.
    if not isinstance(sort, dict):
        return
    sort_objs = sort.get("sortObjs")
    if not isinstance(sort_objs, list):
        received = "undefined" if "sortObjs" not in sort else _js_type(sort_objs)
        _raise_zod([(f"Invalid input: expected array, received {received}", "sort.sortObjs")])
        return
    issues: list[tuple[str, str]] = []
    for i, item in enumerate(sort_objs):
        issues.extend(_sort_item_issues(item, f"sort.sortObjs[{i}]"))
    _raise_zod(issues)


def _validate_group(group: Any) -> None:
    # groupSchema = groupItemSchema.array().nullable(); a null value clears grouping.
    if not isinstance(group, list):
        return
    issues: list[tuple[str, str]] = []
    for i, item in enumerate(group):
        issues.extend(_sort_item_issues(item, f"group[{i}]"))
    _raise_zod(issues)


@router.get("/socket/snapshot-bulk", status_code=200)
@permissions("view|read")
@allow_anonymous()
async def socket_snapshot_bulk(tableId: str, request: Request) -> list[dict[str, Any]]:
    ids = query_list(request.query_params, "ids")
    return await ViewService().socket_snapshot_bulk(tableId, ids)


@router.get("/socket/doc-ids", status_code=200)
@permissions("view|read")
@allow_anonymous()
async def socket_doc_ids(tableId: str, request: Request) -> dict[str, Any]:
    return await ViewService().socket_doc_ids(tableId, dict(request.query_params))


@router.get("/{viewId}/filter-link-records", status_code=200)
@permissions("view|read")
@allow_anonymous()
async def filter_link_records(tableId: str, viewId: str) -> list[dict[str, Any]]:
    return await ViewService().get_filter_link_records(tableId, viewId)


@router.get("/{viewId}", status_code=200)
@permissions("view|read")
@allow_anonymous()
async def get_view(tableId: str, viewId: str) -> dict[str, Any]:
    return await ViewService().get_view(tableId, viewId)


@router.get("", status_code=200)
@permissions("view|read")
@allow_anonymous()
async def list_views(tableId: str) -> list[dict[str, Any]]:
    return await ViewService().list_views(tableId)


@router.post("", status_code=201)
@permissions("view|create")
@allow_anonymous()
async def create_view(tableId: str, request: Request) -> dict[str, Any]:
    body = ViewCreateBody.zod_validate(await read_json_body(request))
    _validate_sort(body.sort)
    _validate_group(body.group)
    return await ViewService().create_view(tableId, body)


@router.post("/plugin", status_code=201)
@permissions("view|create")
@allow_anonymous()
async def install_view_plugin(tableId: str, request: Request) -> dict[str, Any]:
    body = await read_json_body(request)
    return await ViewPluginService().install(tableId, body)


@router.get("/{viewId}/plugin", status_code=200)
@permissions("view|read")
@allow_anonymous()
async def get_view_plugin(tableId: str, viewId: str) -> dict[str, Any]:
    return await ViewPluginService().get(tableId, viewId)


@router.patch("/{viewId}/plugin/{pluginInstallId}", status_code=200)
@permissions("view|update")
@allow_anonymous()
async def update_view_plugin_storage(
    tableId: str, viewId: str, pluginInstallId: str, request: Request
) -> dict[str, Any]:
    body = await read_json_body(request)
    return await ViewPluginService().update_storage(
        tableId, viewId, pluginInstallId, body.get("storage")
    )


@router.delete("/{viewId}", status_code=200)
@permissions("view|delete")
@allow_anonymous()
async def delete_view(tableId: str, viewId: str) -> Response:
    await ViewService().delete_view(tableId, viewId)
    return Response(status_code=200)


@router.put("/{viewId}/name", status_code=200)
@permissions("view|update")
@allow_anonymous()
async def update_name(tableId: str, viewId: str, request: Request) -> Response:
    body = ViewNameBody.zod_validate(await read_json_body(request))
    await ViewService().update_name(tableId, viewId, body)
    return Response(status_code=200)


@router.put("/{viewId}/description", status_code=200)
@permissions("view|update")
@allow_anonymous()
async def update_description(tableId: str, viewId: str, request: Request) -> Response:
    body = ViewDescriptionBody.zod_validate(await read_json_body(request))
    await ViewService().update_description(tableId, viewId, body.description)
    return Response(status_code=200)


@router.put("/{viewId}/locked", status_code=200)
@permissions("view|update")
@allow_anonymous()
async def update_locked(tableId: str, viewId: str, request: Request) -> Response:
    body = ViewLockedBody.zod_validate(await read_json_body(request))
    await ViewService().update_locked(tableId, viewId, body.isLocked)
    return Response(status_code=200)


@router.put("/{viewId}/filter", status_code=200)
@permissions("view|update")
@allow_anonymous()
async def update_filter(tableId: str, viewId: str, request: Request) -> Response:
    body = ViewFilterBody.zod_validate(await read_json_body(request))
    await ViewService().update_json_prop(tableId, viewId, "filter", body.filter)
    return Response(status_code=200)


@router.put("/{viewId}/sort", status_code=200)
@permissions("view|update")
@allow_anonymous()
async def update_sort(tableId: str, viewId: str, request: Request) -> Response:
    body = ViewSortBody.zod_validate(await read_json_body(request))
    _validate_sort(body.sort)
    await ViewService().update_json_prop(tableId, viewId, "sort", body.sort)
    return Response(status_code=200)


@router.put("/{viewId}/group", status_code=200)
@permissions("view|update")
@allow_anonymous()
async def update_group(tableId: str, viewId: str, request: Request) -> Response:
    body = ViewGroupBody.zod_validate(await read_json_body(request))
    _validate_group(body.group)
    await ViewService().update_json_prop(tableId, viewId, "group", body.group)
    return Response(status_code=200)


@router.patch("/{viewId}/options", status_code=200)
@permissions("view|update")
@allow_anonymous()
async def update_options(tableId: str, viewId: str, request: Request) -> Response:
    body = ViewOptionsBody.zod_validate(await read_json_body(request))
    await ViewService().update_options(tableId, viewId, body.options)
    return Response(status_code=200)


@router.put("/{viewId}/column-meta", status_code=200)
@permissions("view|update")
@allow_anonymous()
async def update_column_meta(tableId: str, viewId: str, request: Request) -> Response:
    raw = await read_json_body(request)
    items = [ColumnMetaItem.zod_validate(i) for i in raw] if isinstance(raw, list) else []
    await ViewService().update_column_meta(tableId, viewId, items)
    return Response(status_code=200)


@router.put("/{viewId}/manual-sort", status_code=200)
@permissions("view|update")
@allow_anonymous()
async def manual_sort(tableId: str, viewId: str, request: Request) -> Response:
    body = ManualSortBody.zod_validate(await read_json_body(request))
    await ViewService().update_json_prop(
        tableId, viewId, "sort", {"manualSort": True, "sortObjs": body.sortObjs}, validate=False
    )
    return Response(status_code=200)


@router.put("/{viewId}/order", status_code=200)
@permissions("view|update")
@allow_anonymous()
async def update_order(tableId: str, viewId: str, request: Request) -> Response:
    body = ViewOrderBody.zod_validate(await read_json_body(request))
    await ViewService().update_order(tableId, viewId, body.anchorId, body.position)
    return Response(status_code=200)


@router.put("/{viewId}/record-order", status_code=200)
@permissions("view|update")
@allow_anonymous()
async def update_record_order(tableId: str, viewId: str, request: Request) -> Response:
    body = RecordOrderBody.zod_validate(await read_json_body(request))
    await ViewService().update_record_order(
        tableId, viewId, body.anchorId, body.position, body.recordIds
    )
    return Response(status_code=200)


@router.put("/{viewId}/share-meta", status_code=200)
@permissions("view|share")
@allow_anonymous()
async def update_share_meta(tableId: str, viewId: str, request: Request) -> Response:
    body = ShareMetaBody.zod_validate(await read_json_body(request))
    await ViewService().update_share_meta(tableId, viewId, body)
    return Response(status_code=200)


@router.post("/{viewId}/refresh-share-id", status_code=201)
@permissions("view|share")
@allow_anonymous()
async def refresh_share_id(tableId: str, viewId: str) -> dict[str, Any]:
    return await ViewService().refresh_share_id(tableId, viewId)


@router.post("/{viewId}/enable-share", status_code=201)
@permissions("view|share")
@allow_anonymous()
async def enable_share(tableId: str, viewId: str) -> dict[str, Any]:
    return await ViewService().enable_share(tableId, viewId)


@router.post("/{viewId}/disable-share", status_code=201)
@permissions("view|update")
@allow_anonymous()
async def disable_share(tableId: str, viewId: str) -> Response:
    await ViewService().disable_share(tableId, viewId)
    return Response(status_code=201)


@router.post("/{viewId}/duplicate", status_code=201)
@permissions("view|create")
@allow_anonymous()
async def duplicate_view(tableId: str, viewId: str) -> dict[str, Any]:
    return await ViewService().duplicate_view(tableId, viewId)
