"""Request-scoped context (contextvars), mirroring the upstream IClsStore.

One ContextVar holds a per-request dict; guards and services read/write the
same keys the upstream cls store exposes (``id`` = request id, ``user.*``,
``accessTokenId``, ``baseShare``/``shareViewId``, ``dataDb``, ``audit``,
``permissions``, ``spaceId``, ``template``, ``tempAuthBaseId``, ...).
"""

import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar, Token
from typing import Any, TypedDict


class ClsUser(TypedDict, total=False):
    id: str
    name: str
    email: str
    isAdmin: bool | None


class ClsOrigin(TypedDict, total=False):
    ip: str
    byApi: bool
    userAgent: str
    referer: str
    method: str
    path: str
    via: str


class ClsDataDb(TypedDict, total=False):
    mode: str
    spaceId: str
    connectionId: str
    urlFingerprint: str | None
    displayHost: str | None
    displayDatabase: str | None
    internalSchema: str | None


class ClsShareAuth(TypedDict, total=False):
    shareId: str
    baseId: str
    nodeId: str | None
    tableId: str
    viewId: str


_store: ContextVar[dict[str, Any] | None] = ContextVar("wedoc_cls", default=None)

REQUEST_ID_HEADER = "X-Request-Id"


def enter(initial: dict[str, Any] | None = None) -> Token[dict[str, Any] | None]:
    return _store.set(dict(initial or {}))


def exit(token: Token[dict[str, Any] | None]) -> None:
    _store.reset(token)


def _current() -> dict[str, Any]:
    store = _store.get()
    if store is None:
        store = {}
        _store.set(store)
    return store


def get(key: str, default: Any = None) -> Any:
    """Dot-path read (``user.id``), matching cls.get semantics."""
    store = _current()
    if "." not in key:
        return store.get(key, default)
    node: Any = store
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def set(key: str, value: Any) -> None:
    store = _current()
    if "." not in key:
        store[key] = value
        return
    parts = key.split(".")
    node = store
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


def get_user() -> ClsUser | None:
    return get("user")


def get_request_id() -> str | None:
    # nestjs-cls stores the generated/forwarded request id under the reserved
    # `id` key (ClsModule generateId, seeded from X-Request-Id)
    return get("id")


class ClsMiddleware:
    """Pure ASGI middleware (contextvars propagate reliably, unlike
    BaseHTTPMiddleware); seeds the store with the request id."""

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        raw = headers.get(b"x-request-id")
        request_id = raw.decode() if raw else uuid.uuid4().hex
        initial: dict[str, Any] = {"id": request_id}
        window_raw = headers.get(b"x-window-id")
        if window_raw:
            initial["windowId"] = window_raw.decode()
        token = enter(initial)
        try:
            await self.app(scope, receive, send)
        finally:
            exit(token)
