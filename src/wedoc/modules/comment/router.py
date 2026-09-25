"""Routes for /api/comment/:tableId.

Ports comment-open-api.controller.ts. The controller is @AllowAnonymous
(RESOURCE) with per-route @Permissions; wedoc mirrors this with
@allow_anonymous() + @permissions on each route, enforced by auth_guard +
permission_guard. Route order mirrors upstream so literal second-segment
paths win over the generic /:recordId/:commentId matcher.
"""

import json
from typing import Any

from fastapi import APIRouter, Depends, Request, Response

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
    query: dict[str, Any] = {
        "viewId": params.get("viewId"),
        "filter": _json_param(params.get("filter")),
        "orderBy": _json_param(params.get("orderBy")),
        "groupBy": _json_param(params.get("groupBy")),
        "collapsedGroupIds": _json_param(params.get("collapsedGroupIds")),
        "search": params.getlist("search") or None,
        "ignoreViewQuery": (params.get("ignoreViewQuery") or "").lower() == "true",
    }
    if "take" in params:
        query["take"] = int(params["take"])
    if "skip" in params:
        query["skip"] = int(params["skip"])
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
    params = request.query_params
    take = int(params["take"]) if params.get("take") else 20
    cursor = params.get("cursor")
    direction = params.get("direction") or "forward"
    include_cursor_raw = params.get("includeCursor")
    if include_cursor_raw is None:
        include_cursor = True
    else:
        include_cursor = include_cursor_raw != "false"
    return await CommentService().get_comment_list(
        tableId, recordId, take, cursor, direction, include_cursor
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
