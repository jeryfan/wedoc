"""HTTP middleware replicating the upstream header contract.

Covers: request id (X-Request-Id), helmet v7 default security headers
(HSTS disabled upstream), permissive CORS, session cookie plumbing hooks.
"""

import uuid
from typing import ClassVar

from starlette.datastructures import MutableHeaders
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

CSP = (
    "default-src 'self';base-uri 'self';font-src 'self' https: data:;"
    "form-action 'self';frame-ancestors 'self';img-src 'self' data:;object-src 'none';"
    "script-src 'self';script-src-attr 'none';style-src 'self' https: 'unsafe-inline';"
    "upgrade-insecure-requests"
)

SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Origin-Agent-Cluster": "?1",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-DNS-Prefetch-Control": "off",
    "X-Download-Options": "noopen",
    "X-Frame-Options": "SAMEORIGIN",
    "X-Permitted-Cross-Domain-Policies": "none",
    "X-XSS-Protection": "0",
}


class JsonCharsetMiddleware:
    """NestJS/Express emit `application/json; charset=utf-8`; Starlette omits the
    charset. Rewrite the bare content-type on the response start message so every
    JSON response (success and error, from any router) matches the contract."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(raw=message["headers"])
                if headers.get("content-type") == "application/json":
                    headers["content-type"] = "application/json; charset=utf-8"
            await send(message)

        await self.app(scope, receive, send_wrapper)


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response


class CorsMiddleware(BaseHTTPMiddleware):
    """Matches the `cors` npm package defaults: origin '*', 204 preflight."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method == "OPTIONS" and request.headers.get("access-control-request-method"):
            response = Response(status_code=204)
            response.headers["Access-Control-Allow-Methods"] = "GET,HEAD,PUT,PATCH,POST,DELETE"
            requested = request.headers.get("access-control-request-headers")
            if requested:
                response.headers["Access-Control-Allow-Headers"] = requested
                response.headers["Vary"] = "Access-Control-Request-Headers"
            response.headers["Content-Length"] = "0"
        else:
            response = await call_next(request)
        response.headers.setdefault("Access-Control-Allow-Origin", "*")
        return response


class SessionCsrfMiddleware(BaseHTTPMiddleware):
    """Cross-site protection for session-cookie requests on unsafe methods."""

    _UNSAFE: ClassVar[set[str]] = {"POST", "PUT", "PATCH", "DELETE"}

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        from ..config import get_settings
        from .errors import HttpErrorCode

        settings = get_settings()
        if not settings.backend_session_origin_check_enabled:
            return await call_next(request)
        if request.method.upper() not in self._UNSAFE:
            return await call_next(request)
        if not request.url.path.startswith("/api/"):
            return await call_next(request)
        cookie_header = request.headers.get("cookie", "")
        if not any(
            part.strip().startswith("auth_session=") for part in cookie_header.split(";")
        ):
            return await call_next(request)
        if request.headers.get("authorization", "").lower().startswith("bearer "):
            return await call_next(request)

        from fastapi.responses import JSONResponse

        forbidden = JSONResponse(
            status_code=403,
            content={
                "message": "Cross-site session request is not allowed",
                "status": 403,
                "code": str(HttpErrorCode.RESTRICTED_RESOURCE),
            },
        )
        if request.headers.get("sec-fetch-site") == "cross-site":
            return forbidden
        source = self._normalized_origin(
            request.headers.get("origin")
        ) or self._normalized_origin(request.headers.get("referer"))
        if source and source not in self._allowed_origins(request, settings.public_origin):
            return forbidden
        return await call_next(request)

    @staticmethod
    def _normalized_origin(value: str | None) -> str | None:
        if not value:
            return None
        from urllib.parse import urlparse

        parsed = urlparse(value)
        if not parsed.scheme or not parsed.netloc:
            return None
        return f"{parsed.scheme}://{parsed.netloc}"

    def _allowed_origins(self, request: Request, public_origin: str) -> set[str]:
        origins: set[str] = set()
        normalized = self._normalized_origin(public_origin)
        if normalized:
            origins.add(normalized)
        host = request.headers.get("x-forwarded-host") or request.headers.get("host")
        if host:
            proto = (request.headers.get("x-forwarded-proto") or request.url.scheme).split(",")[
                0
            ].strip()
            origin = self._normalized_origin(f"{proto}://{host}")
            if origin:
                origins.add(origin)
        return origins
