"""Routes for /api/share (public shared-view surface).

Ports share.controller.ts. @Public: no session required. Password-protected
shares carry a share JWT in the cookie named by shareId.
"""

from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from ...core.errors import ApiError, HttpErrorCode
from ...core.query import query_array, query_list
from ...core.validation import read_json_body
from ..aggregation.router import _field_stats, _int_param, _json_param, _search_param, _sort_items
from .service import ShareService

router = APIRouter(prefix="/api/share")


async def _share_info(share_id: str, request: Request) -> dict[str, Any]:
    return await ShareService().resolve_share_info(
        share_id, request.cookies.get(share_id)
    )


@router.post("/{shareId}/view/auth", status_code=200)
async def auth(shareId: str, request: Request) -> Response:
    body = await read_json_body(request)
    password = body.get("password") if isinstance(body, dict) else None
    service = ShareService()
    auth_share_id = await service.auth_share_view(shareId, password)
    if not auth_share_id:
        raise ApiError(
            "Incorrect password.",
            HttpErrorCode.VALIDATION_ERROR,
            {"localization": {"i18nKey": "httpErrors.share.incorrectPassword"}},
        )
    token = service.auth_token(shareId, password)
    response = JSONResponse(content={"token": token}, status_code=200)
    response.set_cookie(
        key=shareId, value=token, max_age=60 * 60 * 24 * 7, httponly=True
    )
    return response


@router.get("/{shareId}/view", status_code=200)
async def get_share_view(shareId: str, request: Request) -> dict[str, Any]:
    info = await _share_info(shareId, request)
    return await ShareService().get_share_view(info)


@router.get("/{shareId}/view/aggregations", status_code=200)
async def get_view_aggregations(shareId: str, request: Request) -> dict[str, Any]:
    info = await _share_info(shareId, request)
    params = request.query_params
    return await ShareService().get_view_aggregations(
        info,
        field_stats=_field_stats(params),
        filter_param=_json_param(params.get("filter"), "filter"),
        group_by=_sort_items(_json_param(params.get("groupBy"), "groupBy"), "groupBy"),
        search=_search_param(params),
    )


@router.get("/{shareId}/view/row-count", status_code=200)
async def get_view_row_count(shareId: str, request: Request) -> dict[str, Any]:
    info = await _share_info(shareId, request)
    params = request.query_params
    return await ShareService().get_view_row_count(
        info,
        filter_param=_json_param(params.get("filter"), "filter"),
        search=_search_param(params),
        selected_record_ids=query_array(params, "selectedRecordIds"),
    )


@router.get("/{shareId}/view/records", status_code=200)
async def get_view_records(shareId: str, request: Request) -> dict[str, Any]:
    info = await _share_info(shareId, request)
    params = request.query_params
    query = {
        "filter": _json_param(params.get("filter"), "filter"),
        "orderBy": _json_param(params.get("orderBy"), "orderBy"),
        "groupBy": _sort_items(_json_param(params.get("groupBy"), "groupBy"), "groupBy"),
        "search": _search_param(params),
        "projection": query_list(params, "projection") or None,
        "take": _int_param(params, "take"),
        "skip": _int_param(params, "skip"),
    }
    return await ShareService().get_view_records(info, query)


@router.get("/{shareId}/view/link-records", status_code=200)
async def get_view_link_records(shareId: str, request: Request) -> list[dict[str, Any]]:
    info = await _share_info(shareId, request)
    params = request.query_params
    field_id = params.get("fieldId")
    if not field_id:
        from ...core.errors import ApiError, HttpErrorCode

        raise ApiError(
            'Validation error: Invalid input: expected string, received undefined '
            'at "fieldId"',
            HttpErrorCode.VALIDATION_ERROR,
        )
    query = {
        "fieldId": field_id,
        "type": params.get("type"),
        "search": params.get("search"),
        "take": _int_param(params, "take"),
        "skip": _int_param(params, "skip"),
    }
    return await ShareService().get_view_link_records(info, query)


@router.get("/{shareId}/view/collaborators", status_code=200)
async def get_view_collaborators(
    shareId: str, request: Request
) -> list[dict[str, Any]]:
    info = await _share_info(shareId, request)
    params = request.query_params
    return await ShareService().get_view_collaborators(
        info,
        {
            "fieldId": params.get("fieldId"),
            "skip": _int_param(params, "skip"),
            "take": _int_param(params, "take"),
            "search": params.get("search"),
            "type": params.get("type"),
        },
    )


@router.get("/{shareId}/view/copy", status_code=200)
async def view_copy(shareId: str, request: Request) -> dict[str, Any]:
    info = await _share_info(shareId, request)
    params = request.query_params
    ranges = _json_param(params.get("ranges"), "ranges")
    if ranges is None:
        raise ApiError(
            'Validation error: Invalid input: expected string, received undefined'
            ' at "ranges"',
            HttpErrorCode.VALIDATION_ERROR,
        )
    query = {
        "ranges": ranges,
        "type": params.get("type"),
        "projection": query_array(params, "projection"),
        "filter": _json_param(params.get("filter"), "filter"),
        "orderBy": _json_param(params.get("orderBy"), "orderBy"),
        "groupBy": _json_param(params.get("groupBy"), "groupBy"),
        "collapsedGroupIds": _json_param(
            params.get("collapsedGroupIds"), "collapsedGroupIds"
        ),
        "search": _search_param(params),
    }
    return await ShareService().copy(info, query)


@router.post(
    "/{shareId}/view/record/{recordId}/{fieldId}/button-click", status_code=201
)
async def view_button_click(
    shareId: str, recordId: str, fieldId: str, request: Request
) -> dict[str, Any]:
    info = await _share_info(shareId, request)
    result = await ShareService().button_click(info, recordId, fieldId)
    return {**result, "runId": ""}


@router.post("/{shareId}/view/form-submit", status_code=201)
async def form_submit(shareId: str, request: Request) -> dict[str, Any]:
    from ..record.schemas import RecordSubmitBody

    info = await _share_info(shareId, request)
    raw = await read_json_body(request)
    if isinstance(raw, dict):
        raw = {**raw, "viewId": info["view"]["id"]}
    body = RecordSubmitBody.zod_validate(raw)
    return await ShareService().form_submit(info, body)


@router.get("/{shareId}/view/group-points", status_code=200)
async def get_view_group_points(shareId: str, request: Request) -> Any:
    info = await _share_info(shareId, request)
    params = request.query_params
    return await ShareService().get_view_group_points(
        info,
        filter_param=_json_param(params.get("filter"), "filter"),
        group_by=_sort_items(_json_param(params.get("groupBy"), "groupBy"), "groupBy"),
        collapsed_ids=_json_param(params.get("collapsedGroupIds"), "collapsedGroupIds"),
    )


@router.get("/{shareId}/view/calendar-daily-collection", status_code=200)
async def get_calendar(shareId: str, request: Request) -> dict[str, Any]:
    info = await _share_info(shareId, request)
    params = request.query_params
    missing = [
        n
        for n in ("startDate", "endDate", "startDateFieldId", "endDateFieldId")
        if not params.get(n)
    ]
    if missing:
        raise ApiError(
            f'Validation error: Invalid input: expected string, received undefined'
            f' at "{missing[0]}"',
            HttpErrorCode.VALIDATION_ERROR,
        )
    return await ShareService().get_calendar_daily_collection(
        info,
        {
            "startDate": params["startDate"],
            "endDate": params["endDate"],
            "startDateFieldId": params["startDateFieldId"],
            "endDateFieldId": params["endDateFieldId"],
            "filter": _json_param(params.get("filter"), "filter"),
            "search": _search_param(params),
        },
    )


@router.get("/{shareId}/view/search-count", status_code=200)
async def get_search_count(shareId: str, request: Request) -> dict[str, Any]:
    info = await _share_info(shareId, request)
    params = request.query_params
    return await ShareService().get_search_count(
        info,
        filter_param=_json_param(params.get("filter"), "filter"),
        search=_search_param(params),
    )


@router.get("/{shareId}/view/search-index", status_code=200)
async def get_search_index(shareId: str, request: Request) -> Any:
    info = await _share_info(shareId, request)
    params = request.query_params
    result = await ShareService().get_search_index(
        info,
        filter_param=_json_param(params.get("filter"), "filter"),
        search=_search_param(params),
        take=_int_param(params, "take"),
        skip=_int_param(params, "skip") or 0,
        order_by=_sort_items(_json_param(params.get("orderBy"), "orderBy"), "orderBy"),
        group_by=_sort_items(_json_param(params.get("groupBy"), "groupBy"), "groupBy"),
    )
    if result is None:
        return Response(status_code=200)
    return result


# -- socket --------------------------------------------------------------------


@router.get("/{shareId}/socket/view/snapshot-bulk", status_code=200)
async def view_snapshot_bulk(shareId: str, request: Request) -> list[dict[str, Any]]:
    info = await _share_info(shareId, request)
    params = request.query_params
    # qs: the bracket/repeated form is an array; a single plain ?ids= is a scalar
    # string. get_view_snapshot_bulk allows only the array form == [view id].
    bracket = params.getlist("ids[]")
    plain = params.getlist("ids")
    if bracket:
        ids: list[str] | str | None = bracket
    elif len(plain) >= 2:
        ids = plain
    elif len(plain) == 1:
        ids = plain[0]
    else:
        ids = None
    return await ShareService().get_view_snapshot_bulk(info, ids)


@router.get("/{shareId}/socket/view/doc-ids", status_code=200)
async def view_doc_ids(shareId: str, request: Request) -> dict[str, Any]:
    info = await _share_info(shareId, request)
    return await ShareService().get_view_doc_ids(info)


@router.get("/{shareId}/socket/field/snapshot-bulk", status_code=200)
async def field_snapshot_bulk(shareId: str, request: Request) -> list[dict[str, Any]]:
    info = await _share_info(shareId, request)
    ids = query_list(request.query_params, "ids")
    return await ShareService().get_field_snapshot_bulk(info, ids)


@router.get("/{shareId}/socket/field/doc-ids", status_code=200)
async def field_doc_ids(shareId: str, request: Request) -> dict[str, Any]:
    info = await _share_info(shareId, request)
    return await ShareService().get_field_doc_ids(info)


@router.get("/{shareId}/socket/computed-activity/authorize", status_code=200)
async def authorize_computed_activity(shareId: str, request: Request) -> Response:
    info = await _share_info(shareId, request)
    table_id = request.query_params.get("tableId")
    if table_id is not None and table_id != info["tableId"]:
        raise ApiError(
            f"Table({table_id}) permission not allowed: read",
            HttpErrorCode.RESTRICTED_RESOURCE,
        )
    return Response(status_code=200)


@router.post("/{shareId}/socket/record/snapshot-bulk", status_code=201)
async def record_snapshot_bulk(shareId: str, request: Request) -> list[dict[str, Any]]:
    info = await _share_info(shareId, request)
    body = await read_json_body(request)
    ids = body.get("ids") or [] if isinstance(body, dict) else []
    projection = body.get("projection") if isinstance(body, dict) else None
    return await ShareService().get_record_snapshot_bulk(info, ids, projection)


@router.post("/{shareId}/socket/record/doc-ids", status_code=201)
async def record_doc_ids(shareId: str, request: Request) -> dict[str, Any]:
    info = await _share_info(shareId, request)
    body = await read_json_body(request)
    query: dict[str, Any] = {}
    if isinstance(body, dict):
        query = {
            "viewId": body.get("viewId"),
            "filter": body.get("filter"),
            "orderBy": body.get("orderBy"),
            "take": body.get("take"),
            "skip": body.get("skip"),
        }
    return await ShareService().get_record_doc_ids(info, query)
