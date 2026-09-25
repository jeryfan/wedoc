"""SockJS server transport (prefix ``/socket``), websocket transport only.

Implements the frames the SockJS client expects on the raw websocket transport:
open ``o``, heartbeat ``h``, array ``a[...]`` (each element a JSON-encoded
message string) and close ``c[code,reason]``. The ``/socket/info`` endpoint
advertises websocket support. ``xhr-streaming`` is not implemented — the
frontend's ReconnectingSockJS prefers websocket, and websocket is the only
transport the deployment target needs (documented in docs/api-parity-ledger.md).
"""

from __future__ import annotations

import asyncio
import json
import secrets
from typing import Any

import structlog
from starlette.websockets import WebSocket, WebSocketDisconnect

from ..core.security.session import SessionHandle, SessionStore
from .pubsub import get_pubsub
from .sharedb import Agent

logger = structlog.get_logger(__name__)

HEARTBEAT_INTERVAL = 25.0


def info_payload() -> dict[str, Any]:
    return {
        "websocket": True,
        "origins": ["*:*"],
        "cookie_needed": False,
        "entropy": secrets.randbits(31),
    }


def _encode_array(messages: list[str]) -> str:
    return "a" + json.dumps(messages, separators=(",", ":"))


class SockJSConnection:
    """Wraps a Starlette websocket with SockJS raw-websocket framing."""

    def __init__(self, websocket: WebSocket) -> None:
        self._ws = websocket
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._closed = False
        self._writer: asyncio.Task[None] | None = None
        self._heartbeat: asyncio.Task[None] | None = None

    async def open(self) -> None:
        await self._ws.accept()
        self._writer = asyncio.create_task(self._write_loop())
        self._heartbeat = asyncio.create_task(self._heartbeat_loop())
        await self._ws.send_text("o")

    def send_message(self, message: dict[str, Any]) -> None:
        if self._closed:
            return
        frame = _encode_array([json.dumps(message, separators=(",", ":"))])
        self._queue.put_nowait(frame)

    async def _write_loop(self) -> None:
        try:
            while True:
                frame = await self._queue.get()
                await self._ws.send_text(frame)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - socket teardown
            pass

    async def _heartbeat_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                await self._ws.send_text("h")
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover
            pass

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for task in (self._heartbeat, self._writer):
            if task is not None:
                task.cancel()

    @staticmethod
    def decode_incoming(payload: str) -> list[dict[str, Any]]:
        """Decode a raw websocket text frame into ShareDB message dicts."""
        if not payload:
            return []
        parsed = json.loads(payload)
        raw_messages = parsed if isinstance(parsed, list) else [parsed]
        result: list[dict[str, Any]] = []
        for item in raw_messages:
            result.append(json.loads(item) if isinstance(item, str) else item)
        return result


async def _resolve_cls_context(websocket: WebSocket) -> dict[str, Any]:
    cookie_header = websocket.headers.get("cookie")
    context: dict[str, Any] = {"cookie": cookie_header}
    try:
        from ..core.cache import get_cache

        handle = SessionHandle(SessionStore(get_cache()))
        sid = handle.session_id_from_cookie_header(cookie_header)
        if sid:
            user_id = await handle.get_user_id(sid)
            if user_id:
                context["user.id"] = user_id
                context["user"] = {"id": user_id}
    except Exception:
        pass
    return context


async def websocket_endpoint(websocket: WebSocket) -> None:
    connection = SockJSConnection(websocket)
    await connection.open()
    cls_context = await _resolve_cls_context(websocket)
    agent = Agent(connection.send_message, get_pubsub(), cls_context)
    try:
        while True:
            payload = await websocket.receive_text()
            for message in SockJSConnection.decode_incoming(payload):
                await agent.handle_message(message)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning("websocket loop error", error=str(exc))
    finally:
        agent.close()
        await connection.close()
