"""UserController + TrackingController + LastVisitController port: /api/user routes."""

from typing import Any

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile

from ...core import cls
from ...core.avatar import AVATAR_ALLOWED_MIMETYPES, AVATAR_MAX_FILE_SIZE
from ...core.errors import ApiError, HttpErrorCode
from ...core.security.auth import auth_guard
from ...core.security.permissions import permission_guard
from ...core.validation import read_json_body
from .last_visit import LastVisitService
from .schemas import (
    GetUserLastVisitBaseNodeQuery,
    GetUserLastVisitQuery,
    TrackEventBody,
    UpdateUserLangBody,
    UpdateUserLastVisitBody,
    UpdateUserNameBody,
    UserNotifyMetaBody,
)
from .service import UserService

router = APIRouter(
    prefix="/api/user",
    dependencies=[Depends(auth_guard), Depends(permission_guard)],
)


def _empty(status_code: int) -> Response:
    return Response(status_code=status_code)


_AVATAR_FILE = File(None)


@router.patch("/name", status_code=200)
async def update_name(request: Request) -> Response:
    body = UpdateUserNameBody.zod_validate(await read_json_body(request))
    await UserService().update_user_name(cls.get("user.id"), body.name)
    return _empty(200)


@router.patch("/avatar", status_code=200)
async def update_avatar(file: UploadFile | None = _AVATAR_FILE) -> Response:
    if file is None:
        # multer leaves the field undefined; upstream dereferences it -> 500
        raise RuntimeError("Cannot read properties of undefined (reading 'path')")
    if file.content_type not in AVATAR_ALLOWED_MIMETYPES:
        raise ApiError(
            "Unsupported file type. Only JPEG, PNG, and WebP are allowed.",
            HttpErrorCode.VALIDATION_ERROR,
        )
    data = await file.read()
    if len(data) > AVATAR_MAX_FILE_SIZE:
        # multer fileSize limit surfaces as an unhandled MulterError -> 500
        raise RuntimeError("File too large")
    await UserService().update_avatar(cls.get("user.id"), data)
    return _empty(200)


@router.patch("/notify-meta", status_code=200)
async def update_notify_meta(request: Request) -> Response:
    body = UserNotifyMetaBody.zod_validate(await read_json_body(request))
    await UserService().update_notify_meta(
        cls.get("user.id"), body.model_dump(exclude_unset=True)
    )
    return _empty(200)


@router.patch("/lang", status_code=200)
async def update_lang(request: Request) -> Response:
    body = UpdateUserLangBody.zod_validate(await read_json_body(request))
    await UserService().update_lang(cls.get("user.id"), body.lang)
    return _empty(200)


# Allowed frontend events (whitelist to prevent arbitrary span pollution)
_TRACK_ALLOWED_EVENTS = {
    "view.open",
    "record.expand",
    "filter.apply",
    "sort.apply",
    "search.execute",
    "app.view",
    "app.page_view",
}


@router.post("/track", status_code=204)
async def track(request: Request) -> Response:
    TrackEventBody.zod_validate(await read_json_body(request))
    return _empty(204)


@router.get("/last-visit", status_code=200)
async def get_user_last_visit(request: Request) -> Any:
    query = GetUserLastVisitQuery.zod_validate(dict(request.query_params))
    result = await LastVisitService().get_user_last_visit(
        cls.get("user.id"), query.resourceType, query.parentResourceId
    )
    if result is None:
        return _empty(200)
    return result


@router.post("/last-visit", status_code=201)
async def update_user_last_visit(request: Request) -> Response:
    body = UpdateUserLastVisitBody.zod_validate(await read_json_body(request))
    await LastVisitService().update_user_last_visit(
        cls.get("user.id"), body.model_dump(exclude_unset=True)
    )
    return _empty(201)


@router.get("/last-visit/map", status_code=200)
async def get_user_last_visit_map(request: Request) -> dict[str, Any]:
    query = GetUserLastVisitQuery.zod_validate(dict(request.query_params))
    return await LastVisitService().get_user_last_visit_map(
        cls.get("user.id"), query.parentResourceId
    )


@router.get("/last-visit/list-base", status_code=200)
async def get_user_last_visit_list_base() -> dict[str, Any]:
    return await LastVisitService().list_base(cls.get("user.id"))


@router.get("/last-visit/base-node", status_code=200)
async def get_user_last_visit_base_node(request: Request) -> Any:
    query = GetUserLastVisitBaseNodeQuery.zod_validate(dict(request.query_params))
    result = await LastVisitService().base_node_visit(
        cls.get("user.id"), query.parentResourceId
    )
    if result is None:
        return _empty(200)
    return result
