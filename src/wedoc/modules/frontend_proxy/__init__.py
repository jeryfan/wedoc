"""Front-door concerns the reference backend's embedded Next server handled:
POST /api/query-params, and reverse-proxying page routes / assets / plugin
iframe to the Next.js frontend container (WEDOC_WEB_ORIGIN).

Registered LAST so every /api/* and /socket route wins first.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from ...config import get_settings
from ...core.cache import get_cache
from ...core.ids import IdPrefix, new_id

router = APIRouter()

QUERY_PARAMS_TTL = 60

# Page prefixes the reference NextController serves (next.controller.ts). A
# request whose first path segment is here is proxied to the frontend; anything
# else (unmatched /api, stray paths) gets a 404 in the upstream error shape.
_PAGE_ROOTS = {
    "",
    "favicon.ico",
    "home",
    "_next",
    "__nextjs",
    "images",
    "streamsaver",
    "404",
    "403",
    "402",
    "space",
    "auth",
    "waitlist",
    "base",
    "invite",
    "share",
    "setting",
    "admin",
    "oauth",
    "developer",
    "public",
    "enterprise",
    "unsubscribe",
    "integrations",
    "t",
    "s",
    "artifact",
}

_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "transfer-encoding",
    "te",
    "trailer",
    "upgrade",
    "proxy-authorization",
    "proxy-authenticate",
    "content-length",
    "host",
}


@router.post("/api/query-params")
async def save_query_params(request: Request) -> Response:
    body = await request.json()
    params = body.get("params") if isinstance(body, dict) else None
    if not isinstance(params, dict):
        return JSONResponse(
            status_code=400,
            content={
                "message": 'Validation error: Required at "params"',
                "status": 400,
                "code": "validation_error",
            },
        )
    query_id = new_id(IdPrefix.QUERY)
    await get_cache().set_detail(f"query-params:{query_id}", params, QUERY_PARAMS_TTL)
    return JSONResponse({"queryId": query_id})


def _proxied(path: str) -> bool:
    root = path.split("/", 1)[0]
    return root in _PAGE_ROOTS


@router.api_route(
    "/{full_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
async def frontend_catch_all(full_path: str, request: Request) -> Response:
    settings = get_settings()
    origin = settings.plugin_server_origin if full_path.startswith("plugin") else None
    if origin is None and _proxied(full_path):
        origin = settings.wedoc_web_origin
    if origin is None:
        # Express `Cannot <METHOD> <originalUrl>` includes the raw query string.
        target = f"/{full_path}"
        if request.url.query:
            target = f"{target}?{request.url.query}"
        return JSONResponse(
            status_code=404,
            content={
                "message": f"Cannot {request.method} {target}",
                "status": 404,
                "code": "not_found",
            },
        )
    return await _reverse_proxy(request, origin, full_path)


async def _reverse_proxy(request: Request, origin: str, full_path: str) -> Response:
    url = origin.rstrip("/") + "/" + full_path
    fwd_headers = {
        k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP
    }
    body = await request.body()
    async with httpx.AsyncClient(follow_redirects=False, timeout=30.0) as client:
        upstream = await client.request(
            request.method,
            url,
            params=request.query_params,
            headers=fwd_headers,
            content=body,
        )
    resp_headers = {
        k: v
        for k, v in upstream.headers.items()
        # httpx already transparently decompressed the body, so the original
        # content-encoding/length would make the browser try to gunzip plain
        # bytes (ERR_CONTENT_DECODING_FAILED). Drop them; let the server re-add.
        if k.lower() not in _HOP_BY_HOP and k.lower() != "content-encoding"
    }
    # mitm.html / service worker need relaxed headers, mirroring NextController
    if full_path == "streamsaver/mitm.html":
        resp_headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; frame-ancestors *"
        )
    elif full_path == "streamsaver/sw.js":
        resp_headers["Content-Type"] = "application/javascript; charset=utf-8"
        resp_headers["Service-Worker-Allowed"] = "/"
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=resp_headers,
    )
