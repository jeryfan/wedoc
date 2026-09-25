"""Routes for /api/table/:tableId/aggregation.

Ports aggregation-open-api.controller.ts: footer/statistic aggregations,
row count, record/search index, group points, calendar daily collection,
selection slice aggregation and the static task status collection.
"""

import json
from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from ...core.errors import ApiError, HttpErrorCode
from ...core.security.auth import allow_anonymous, auth_guard, permissions
from ...core.security.permissions import permission_guard
from .service import AggregationService

router = APIRouter(
    prefix="/api/table/{tableId}/aggregation",
    dependencies=[Depends(auth_guard), Depends(permission_guard)],
)


def _json_param(raw: str | None, name: str) -> Any:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise ApiError(
            f'Validation error: Invalid input: expected object at "{name}"',
            HttpErrorCode.VALIDATION_ERROR,
        ) from None


def _sort_items(raw: Any, name: str) -> list[dict[str, Any]] | None:
    # groupBy/orderBy: JSON list of {fieldId, order}; order is required and
    # must be asc|desc (zod enum, per-index error messages joined by "; ").
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ApiError(
            f'Validation error: Invalid input: expected array, received string at "{name}"',
            HttpErrorCode.VALIDATION_ERROR,
        )
    errors = []
    items = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            errors.append(
                f'Invalid input: expected object, received string at "{name}[{index}]"'
            )
            continue
        order = item.get("order")
        if order not in ("asc", "desc"):
            errors.append(
                f'Invalid option: expected one of "asc"|"desc" at "{name}[{index}].order"'
            )
        items.append({"fieldId": item.get("fieldId", ""), "order": order or "asc"})
    if errors:
        raise ApiError(
            f"Validation error: {'; '.join(errors)}", HttpErrorCode.VALIDATION_ERROR
        )
    return items


def _field_stats(params: Any) -> list[tuple[str, list[str]]] | None:
    # bracketed record format: field[sum][]=fld... (array values required).
    stats: list[tuple[str, list[str]]] = []
    seen: list[str] = []
    for key in params.keys():
        if not key.startswith("field["):
            continue
        rest = key[len("field[") :]
        if rest.endswith("][]"):
            func = rest[:-3]
        elif rest.endswith("]"):
            raise ApiError(
                f'Validation error: Invalid input: expected array, received string'
                f' at "field.{rest[:-1]}"',
                HttpErrorCode.VALIDATION_ERROR,
            )
        else:
            continue
        if func in seen:
            continue
        seen.append(func)
        values = params.getlist(key)
        if any(not isinstance(v, str) for v in values):
            raise ApiError(
                'Validation error: Invalid input: expected array at "field"',
                HttpErrorCode.VALIDATION_ERROR,
            )
        stats.append((func, list(values)))
    if stats:
        return stats
    if "field" in params:
        raise ApiError(
            'Validation error: Invalid input: expected record, received string at "field"',
            HttpErrorCode.VALIDATION_ERROR,
        )
    return None


def _search_param(params: Any) -> list[str] | None:
    raw = params.getlist("search")
    if not raw:
        return None
    if len(raw) < 2:
        raise ApiError(
            'Validation error: Invalid input: expected tuple, received string at "search"',
            HttpErrorCode.VALIDATION_ERROR,
        )
    return list(raw)


def _ignore_view_query(params: Any) -> bool:
    return (params.get("ignoreViewQuery") or "").lower() not in ("", "false")


def _tql_or_filter(params: Any) -> tuple[dict[str, Any] | None, str | None]:
    filter_param = _json_param(params.get("filter"), "filter")
    tql = params.get("filterByTql")
    return filter_param, tql


def _int_param(params: Any, name: str) -> int | None:
    raw = params.get(name)
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        raise ApiError(
            f'Validation error: Invalid input: expected number, received NaN at "{name}"',
            HttpErrorCode.VALIDATION_ERROR,
        ) from None


@router.get("", status_code=200)
@permissions("table|read")
@allow_anonymous()
async def get_aggregation(tableId: str, request: Request) -> dict[str, Any]:
    params = request.query_params
    filter_param, tql = _tql_or_filter(params)
    return await AggregationService().get_aggregation(
        tableId,
        field_stats=_field_stats(params),
        filter_param=filter_param,
        tql=tql,
        search=_search_param(params),
        view_id=params.get("viewId"),
        group_by=_sort_items(_json_param(params.get("groupBy"), "groupBy"), "groupBy"),
        ignore_view_query=_ignore_view_query(params),
    )


@router.get("/row-count", status_code=200)
@permissions("table|read")
@allow_anonymous()
async def get_row_count(tableId: str, request: Request) -> dict[str, Any]:
    params = request.query_params
    filter_param, tql = _tql_or_filter(params)
    selected = params.getlist("selectedRecordIds")
    if "selectedRecordIds" in params and len(selected) < 2:
        raise ApiError(
            'Validation error: Invalid input: expected array, received string'
            ' at "selectedRecordIds"',
            HttpErrorCode.VALIDATION_ERROR,
        )
    return await AggregationService().get_row_count(
        tableId,
        filter_param=filter_param,
        tql=tql,
        search=_search_param(params),
        view_id=params.get("viewId"),
        selected_record_ids=selected or None,
        ignore_view_query=_ignore_view_query(params),
    )


@router.get("/record-index", status_code=200)
@permissions("table|read")
@allow_anonymous()
async def get_record_index(tableId: str, request: Request):
    params = request.query_params
    record_id = params.get("recordId")
    if record_id is None:
        raise ApiError(
            'Validation error: Invalid input: expected string, received undefined at "recordId"',
            HttpErrorCode.VALIDATION_ERROR,
        )
    result = await AggregationService().get_record_index(
        tableId,
        record_id=record_id,
        filter_param=_json_param(params.get("filter"), "filter"),
        order_by=_sort_items(_json_param(params.get("orderBy"), "orderBy"), "orderBy"),
        view_id=params.get("viewId"),
        ignore_view_query=_ignore_view_query(params),
    )
    if result is None:
        return Response(status_code=200)
    return result


@router.get("/search-count", status_code=200)
@permissions("table|read")
@allow_anonymous()
async def get_search_count(tableId: str, request: Request) -> dict[str, Any]:
    params = request.query_params
    return await AggregationService().get_search_count(
        tableId,
        filter_param=_json_param(params.get("filter"), "filter"),
        search=_search_param(params),
        view_id=params.get("viewId"),
        ignore_view_query=_ignore_view_query(params),
    )


@router.get("/search-index", status_code=200)
@permissions("table|read")
@allow_anonymous()
async def get_search_index(tableId: str, request: Request):
    params = request.query_params
    skip = _int_param(params, "skip") or 0
    result = await AggregationService().get_search_index(
        tableId,
        filter_param=_json_param(params.get("filter"), "filter"),
        search=_search_param(params),
        take=_int_param(params, "take"),
        skip=skip,
        order_by=_sort_items(_json_param(params.get("orderBy"), "orderBy"), "orderBy"),
        group_by=_sort_items(_json_param(params.get("groupBy"), "groupBy"), "groupBy"),
        view_id=params.get("viewId"),
        ignore_view_query=_ignore_view_query(params),
    )
    if result is None:
        return Response(status_code=200)
    return result


@router.get("/group-points", status_code=200)
@permissions("table|read")
@allow_anonymous()
async def get_group_points(tableId: str, request: Request) -> list[dict[str, Any]]:
    params = request.query_params
    filter_param, tql = _tql_or_filter(params)
    collapsed_raw = _json_param(params.get("collapsedGroupIds"), "collapsedGroupIds")
    return await AggregationService().get_group_points(
        tableId,
        filter_param=filter_param,
        tql=tql,
        search=_search_param(params),
        group_by=_sort_items(_json_param(params.get("groupBy"), "groupBy"), "groupBy"),
        collapsed_ids=collapsed_raw,
        view_id=params.get("viewId"),
        ignore_view_query=_ignore_view_query(params),
    )


@router.get("/calendar-daily-collection", status_code=200)
@permissions("table|read")
@allow_anonymous()
async def get_calendar_daily_collection(tableId: str, request: Request) -> dict[str, Any]:
    params = request.query_params
    missing = [
        name
        for name in ("startDate", "endDate", "startDateFieldId", "endDateFieldId")
        if not params.get(name)
    ]
    if missing:
        raise ApiError(
            f'Validation error: Invalid input: expected string, received undefined'
            f' at "{missing[0]}"',
            HttpErrorCode.VALIDATION_ERROR,
        )
    return await AggregationService().get_calendar_daily_collection(
        tableId,
        start_date=params["startDate"],
        end_date=params["endDate"],
        start_field_id=params["startDateFieldId"],
        end_field_id=params["endDateFieldId"],
        filter_param=_json_param(params.get("filter"), "filter"),
        search=_search_param(params),
        view_id=params.get("viewId"),
        ignore_view_query=_ignore_view_query(params),
    )


@router.get("/selection", status_code=200)
@permissions("table|read")
@allow_anonymous()
async def get_selection_aggregation(tableId: str, request: Request) -> dict[str, Any]:
    params = request.query_params
    filter_param, tql = _tql_or_filter(params)
    return await AggregationService().get_selection_aggregation(
        tableId,
        field_stats=_field_stats(params),
        filter_param=filter_param,
        tql=tql,
        search=_search_param(params),
        view_id=params.get("viewId"),
        order_by=_sort_items(_json_param(params.get("orderBy"), "orderBy"), "orderBy"),
        group_by=_sort_items(_json_param(params.get("groupBy"), "groupBy"), "groupBy"),
        skip=_int_param(params, "skip") or 0,
        take=_int_param(params, "take"),
        ignore_view_query=_ignore_view_query(params),
    )


@router.get("/task-status-collection", status_code=200)
@permissions("table|read")
@allow_anonymous()
async def get_task_status_collection(tableId: str) -> dict[str, Any]:
    return {"fieldMap": {}, "cells": []}
