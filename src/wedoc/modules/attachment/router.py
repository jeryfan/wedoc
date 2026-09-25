"""Routes for /api/attachments.

Controller is @Public; signature/notify require auth, or the shared-view auth
path when a Tea-Share-Id header is present (anonymous public-form uploads).
upload/read are token-authenticated and public. read supports
If-Modified-Since / 304.
"""

from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from ...core.validation import read_json_body
from .guard import signature_notify_guard
from .schemas import SignatureRo
from .service import AttachmentService

# authenticated: signature + notify (share visitors resolved via Tea-Share-Id)
router = APIRouter(
    prefix="/api/attachments", dependencies=[Depends(signature_notify_guard)]
)

# public: upload (token) + read (token)
public_router = APIRouter(prefix="/api/attachments")


@router.post("/signature", status_code=201)
async def signature(request: Request) -> dict[str, Any]:
    body = SignatureRo.zod_validate(await read_json_body(request))
    return await AttachmentService().signature(body)


@router.post("/notify/{token}", status_code=201)
async def notify(token: str, request: Request) -> dict[str, Any]:
    filename = request.query_params.get("filename")
    return await AttachmentService().notify(token, filename)


@public_router.put("/upload/{token}", status_code=200)
async def upload_put(token: str, request: Request) -> Response:
    data = await request.body()
    await AttachmentService().upload(data, request.headers.get("content-type"), token)
    return Response(status_code=200)


@public_router.post("/upload/{token}", status_code=201)
async def upload_post(token: str, request: Request) -> Response:
    data = await request.body()
    await AttachmentService().upload(data, request.headers.get("content-type"), token)
    return Response(status_code=201)


@public_router.get("/read/{path:path}", status_code=200)
async def read(path: str, request: Request) -> Response:
    service = AttachmentService()
    token = request.query_params.get("token")
    disposition = request.query_params.get("response-content-disposition")
    base_headers = {
        "Cross-Origin-Resource-Policy": "unsafe-none",
        "Content-Security-Policy": "",
    }
    has_cache, last_modified = service.conditional_caching(
        path, request.headers.get("if-modified-since")
    )
    if has_cache:
        return Response(status_code=304, headers=base_headers)
    content, file_headers = await service.read_local_file(path, token)
    headers = {**base_headers, **file_headers}
    if last_modified:
        headers["Last-Modified"] = last_modified
    if disposition:
        headers["Content-Disposition"] = disposition
    media_type = file_headers.get("Content-Type")
    return Response(content=content, headers=headers, media_type=media_type)
