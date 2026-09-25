"""Routes for /api/notifications.

Ports notification.controller.ts. All routes require an authenticated user;
no @Permissions decorator upstream.
"""

from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from ...core import cls
from ...core.security.auth import auth_guard
from ...core.security.permissions import permission_guard
from ...core.validation import read_json_body
from .schemas import GetNotifyListQuery, UpdateNotifyStatusRo
from .service import NotificationService

router = APIRouter(
    prefix="/api/notifications",
    dependencies=[Depends(auth_guard), Depends(permission_guard)],
)


@router.get("", status_code=200)
async def get_notify_list(request: Request) -> dict[str, Any]:
    params = request.query_params
    raw: dict[str, Any] = {}
    for key in ("notifyStates", "severity", "notifyType", "cursor"):
        if key in params:
            raw[key] = params[key]
    query = GetNotifyListQuery.zod_validate(raw)
    user_id = cls.get("user.id")
    return await NotificationService().get_notify_list(user_id, query)


@router.get("/unread-count", status_code=200)
async def unread_count() -> dict[str, int]:
    user_id = cls.get("user.id")
    return await NotificationService().unread_count(user_id)


@router.patch("/read-all", status_code=200)
async def mark_all_as_read() -> Response:
    user_id = cls.get("user.id")
    await NotificationService().mark_all_as_read(user_id)
    return Response(status_code=200)


@router.patch("/{notificationId}/status", status_code=200)
async def update_notify_status(notificationId: str, request: Request) -> Response:
    ro = UpdateNotifyStatusRo.zod_validate(await read_json_body(request))
    user_id = cls.get("user.id")
    await NotificationService().update_notify_status(user_id, notificationId, ro)
    return Response(status_code=200)
