"""Routes for /api/comment/:tableId.

Ports comment-open-api.controller.ts. The controller is @AllowAnonymous
(RESOURCE) with per-route @Permissions; wedoc mirrors this with
@allow_anonymous() + @permissions on each route, enforced by auth_guard +
permission_guard. Route order mirrors upstream so literal second-segment
paths win over the generic /:recordId/:commentId matcher.
"""

import json
import math
import re
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from starlette.datastructures import QueryParams

from ...core.errors import ApiError, HttpErrorCode
from ...core.query import query_array
from ...core.security.auth import allow_anonymous, auth_guard, permissions
from ...core.security.permissions import permission_guard
from ...core.validation import read_json_body
from .schemas import CreateCommentRo, UpdateCommentReactionRo, UpdateCommentRo
from .service import CommentService

router = APIRouter(
    prefix="/api/comment/{tableId}",
    dependencies=[Depends(auth_guard), Depends(permission_guard)],
)


def _json_param(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


# Verbatim zod-validation-error output for each getCommentListQueryRoSchema /
# getRecordsRoSchema failing branch; kept literal so wire messages stay identical.
_TAKE_NAN_ERROR = 'Invalid input: expected number, received NaN at "take"'
_TAKE_MIN_ERROR = 'You should at least take 1 record at "take"'
_TAKE_MAX_ERROR = "Can't take more than 1000 records, please reduce take count at \"take\""
_SKIP_NAN_ERROR = 'Invalid input: expected number, received NaN at "skip"'
_SKIP_MIN_ERROR = 'You can not skip a negative count of records at "skip"'
_DIRECTION_ERROR = (
    'Invalid input: expected "forward" at "direction" or '
    'Invalid input: expected "backward" at "direction"'
)
_INCLUDE_CURSOR_ERROR = (
    'Invalid input: expected boolean, received string at "includeCursor" or '
    'Invalid option: expected one of "true"|"false" at "includeCursor"'
)

# zod coerces take/skip through String(Number(x)); a non-numeric string becomes
# NaN, which the piped z.number() rejects as invalid_type.
_JS_NUMBER_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")


def _js_number(raw: str) -> float:
    text = raw.strip()
    if not text:
        return 0.0
    if not _JS_NUMBER_RE.match(text):
        return float("nan")
    return float(text)


def _page_number(value: float) -> int | float:
    return int(value) if value.is_integer() else value


def _validate_take(raw: str, default: int, errors: list[str]) -> int | float:
    number = _js_number(raw)
    if math.isnan(number):
        errors.append(_TAKE_NAN_ERROR)
        return default
    if number < 1:
        errors.append(_TAKE_MIN_ERROR)
        return default
    if number > 1000:
        errors.append(_TAKE_MAX_ERROR)
        return default
    return _page_number(number)


def _validate_skip(raw: str, errors: list[str]) -> int | float:
    number = _js_number(raw)
    if math.isnan(number):
        errors.append(_SKIP_NAN_ERROR)
        return 0
    if number < 0:
        errors.append(_SKIP_MIN_ERROR)
        return 0
    return _page_number(number)


def _raise_query_errors(errors: list[str]) -> None:
    if errors:
        raise ApiError(
            "Validation error: " + "; ".join(errors), HttpErrorCode.VALIDATION_ERROR
        )


def _parse_comment_list_query(params: QueryParams) -> dict[str, Any]:
    errors: list[str] = []
    take: int | float = 20
    if "take" in params:
        take = _validate_take(params["take"], 20, errors)
    include_cursor = True
    raw_include_cursor = params.get("includeCursor")
    if raw_include_cursor is not None:
        if raw_include_cursor == "true":
            include_cursor = True
        elif raw_include_cursor == "false":
            include_cursor = False
        else:
            errors.append(_INCLUDE_CURSOR_ERROR)
    direction = "forward"
    raw_direction = params.get("direction")
    if raw_direction is not None:
        if raw_direction in ("forward", "backward"):
            direction = raw_direction
        else:
            errors.append(_DIRECTION_ERROR)
    _raise_query_errors(errors)
    return {
        "take": take,
        "cursor": params.get("cursor"),
        "direction": direction,
        "includeCursor": include_cursor,
    }


def _parse_count_pagination(params: QueryParams) -> tuple[int | float, int | float]:
    errors: list[str] = []
    take: int | float = 100
    if "take" in params:
        take = _validate_take(params["take"], 100, errors)
    skip: int | float = 0
    if "skip" in params:
        skip = _validate_skip(params["skip"], errors)
    _raise_query_errors(errors)
    return take, skip


@router.get("/{recordId}/count", status_code=200)
@permissions("record|read")
@allow_anonymous()
async def get_record_comment_count(tableId: str, recordId: str) -> dict[str, int]:
    return await CommentService().get_record_comment_count(tableId, recordId)


@router.get("/count", status_code=200)
@permissions("view|read")
@allow_anonymous()
async def get_table_comment_count(tableId: str, request: Request) -> list[dict[str, Any]]:
    params = request.query_params
    take, skip = _parse_count_pagination(params)
    query: dict[str, Any] = {
        "viewId": params.get("viewId"),
        "filter": _json_param(params.get("filter")),
        "orderBy": _json_param(params.get("orderBy")),
        "groupBy": _json_param(params.get("groupBy")),
        "collapsedGroupIds": _json_param(params.get("collapsedGroupIds")),
        "search": query_array(params, "search", expected="tuple"),
        "ignoreViewQuery": (params.get("ignoreViewQuery") or "").lower() == "true",
        "take": take,
        "skip": skip,
    }
    return await CommentService().get_table_comment_count(tableId, query)


@router.get("/{recordId}/attachment/{path}", status_code=200)
@permissions("record|read")
@allow_anonymous()
async def get_attachment_presigned_url(tableId: str, recordId: str, path: str) -> str:
    return await CommentService().get_attachment_presigned_url(path)


@router.get("/{recordId}/subscribe", status_code=200)
@permissions("record|read")
@allow_anonymous()
async def get_subscribe_detail(tableId: str, recordId: str) -> Any:
    result = await CommentService().get_subscribe_detail(tableId, recordId)
    if result is None:
        return Response(status_code=200)
    return result


@router.post("/{recordId}/subscribe", status_code=201)
@permissions("record|read")
@allow_anonymous()
async def subscribe_comment(tableId: str, recordId: str) -> Response:
    await CommentService().subscribe_comment(tableId, recordId)
    return Response(status_code=201)


@router.delete("/{recordId}/subscribe", status_code=200)
@permissions("record|read")
@allow_anonymous()
async def unsubscribe_comment(tableId: str, recordId: str) -> Response:
    await CommentService().unsubscribe_comment(tableId, recordId)
    return Response(status_code=200)


@router.get("/{recordId}/list", status_code=200)
@permissions("record|read")
@allow_anonymous()
async def get_comment_list(tableId: str, recordId: str, request: Request) -> dict[str, Any]:
    query = _parse_comment_list_query(request.query_params)
    return await CommentService().get_comment_list(
        tableId,
        recordId,
        query["take"],
        query["cursor"],
        query["direction"],
        query["includeCursor"],
    )


@router.post("/{recordId}/create", status_code=201)
@permissions("record|comment")
@allow_anonymous()
async def create_comment(tableId: str, recordId: str, request: Request) -> dict[str, Any]:
    body = CreateCommentRo.zod_validate(await read_json_body(request))
    content = [c.model_dump(mode="json", exclude_none=True) for c in body.content]
    return await CommentService().create_comment(tableId, recordId, body.quoteId, content)


@router.get("/{recordId}/{commentId}", status_code=200)
@permissions("record|read")
@allow_anonymous()
async def get_comment_detail(tableId: str, recordId: str, commentId: str) -> Any:
    result = await CommentService().get_comment_detail(tableId, recordId, commentId)
    if result is None:
        return Response(status_code=200)
    return result


@router.patch("/{recordId}/{commentId}", status_code=200)
@permissions("record|comment")
@allow_anonymous()
async def update_comment(
    tableId: str, recordId: str, commentId: str, request: Request
) -> Response:
    body = UpdateCommentRo.zod_validate(await read_json_body(request))
    content = [c.model_dump(mode="json", exclude_none=True) for c in body.content]
    await CommentService().update_comment(tableId, recordId, commentId, content)
    return Response(status_code=200)


@router.delete("/{recordId}/{commentId}", status_code=200)
@permissions("record|comment")
@allow_anonymous()
async def delete_comment(tableId: str, recordId: str, commentId: str) -> Response:
    await CommentService().delete_comment(tableId, recordId, commentId)
    return Response(status_code=200)


@router.delete("/{recordId}/{commentId}/reaction", status_code=200)
@permissions("record|comment")
@allow_anonymous()
async def delete_comment_reaction(
    tableId: str, recordId: str, commentId: str, request: Request
) -> Response:
    body = UpdateCommentReactionRo.zod_validate(await read_json_body(request))
    await CommentService().delete_comment_reaction(
        tableId, recordId, commentId, body.reaction
    )
    return Response(status_code=200)


@router.patch("/{recordId}/{commentId}/reaction", status_code=200)
@permissions("record|comment")
@allow_anonymous()
async def update_comment_reaction(
    tableId: str, recordId: str, commentId: str, request: Request
) -> Response:
    body = UpdateCommentReactionRo.zod_validate(await read_json_body(request))
    await CommentService().create_comment_reaction(
        tableId, recordId, commentId, body.reaction
    )
    return Response(status_code=200)
