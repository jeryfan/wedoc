"""Pub/sub fan-out for ShareDB streams.

Delivery is in-process first: ``publish`` hands the message straight to the
local subscriber streams, so a REST mutation and the websocket connections it
notifies (same process) never depend on a Redis round-trip. The same message is
also mirrored to Redis — channels unprefixed (``collection``,
``collection.docId``, ``$presence.<channel>``) to match the upstream wire
format — so additional instances receive it; the Redis observer skips messages
this instance published (tagged by origin) to avoid double delivery.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import Callable
from typing import Any

import redis.asyncio as aioredis
import structlog

from ..config import get_settings

logger = structlog.get_logger(__name__)


class Stream:
    """A per-subscription delivery channel consumed by a ShareDB agent."""

    def __init__(self, pubsub: RealtimePubSub, channel: str) -> None:
        self._pubsub = pubsub
        self.channel = channel
        self.on_data: Callable[[dict[str, Any]], None] | None = None
        self.destroyed = False

    def push(self, data: dict[str, Any]) -> None:
        if self.destroyed or self.on_data is None:
            return
        self.on_data(data)

    def destroy(self) -> None:
        if self.destroyed:
            return
        self.destroyed = True
        self._pubsub._remove_stream(self.channel, self)


class RealtimePubSub:
    def __init__(self, redis_uri: str) -> None:
        self._uri = redis_uri
        self._origin = secrets.token_hex(8)
        self._client: aioredis.Redis | None = None
        self._observer: aioredis.Redis | None = None
        self._pubsub: Any = None
        self._streams: dict[str, set[Stream]] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._pending: set[asyncio.Task[None]] = set()
        self._lock = asyncio.Lock()

    async def _ensure(self) -> None:
        if self._client is None:
            self._client = aioredis.from_url(self._uri)
            self._observer = aioredis.from_url(self._uri)
            self._pubsub = self._observer.pubsub()

    def _ensure_reader(self) -> None:
        if self._reader_task is None:
            self._reader_task = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        assert self._pubsub is not None
        try:
            async for message in self._pubsub.listen():
                if message is None or message.get("type") != "message":
                    continue
                channel = message["channel"]
                if isinstance(channel, bytes):
                    channel = channel.decode()
                raw = message["data"]
                if isinstance(raw, bytes):
                    raw = raw.decode()
                try:
                    envelope = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                # Skip our own publishes: they were already delivered locally.
                if envelope.get("_o") == self._origin:
                    continue
                self._emit(channel, envelope.get("d", {}))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - transport level
            logger.warning("pubsub read loop error", error=str(exc))

    def _emit(self, channel: str, data: dict[str, Any]) -> None:
        for stream in list(self._streams.get(channel, ())):
            stream.push(data)

    async def subscribe(self, channel: str) -> Stream:
        await self._ensure()
        stream = Stream(self, channel)
        async with self._lock:
            first = channel not in self._streams
            self._streams.setdefault(channel, set()).add(stream)
            if first and self._pubsub is not None:
                await self._pubsub.subscribe(channel)
                self._ensure_reader()
        return stream

    def _remove_stream(self, channel: str, stream: Stream) -> None:
        streams = self._streams.get(channel)
        if not streams:
            return
        streams.discard(stream)
        if not streams:
            self._streams.pop(channel, None)
            if self._pubsub is not None:
                task = asyncio.create_task(self._safe_unsubscribe(channel))
                self._pending.add(task)
                task.add_done_callback(self._pending.discard)

    async def _safe_unsubscribe(self, channel: str) -> None:
        try:
            if self._pubsub is not None and channel not in self._streams:
                await self._pubsub.unsubscribe(channel)
        except Exception:  # pragma: no cover
            pass

    async def publish(self, channels: list[str], data: dict[str, Any]) -> None:
        # In-process delivery first: never depends on the Redis round-trip.
        for channel in channels:
            self._emit(channel, data)
        await self._ensure()
        assert self._client is not None
        payload = json.dumps({"_o": self._origin, "d": data}, separators=(",", ":"))
        for channel in channels:
            try:
                await self._client.publish(channel, payload)
            except Exception as exc:  # pragma: no cover - cross-instance only
                logger.warning("pubsub publish error", error=str(exc))

    async def close(self) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._pubsub is not None:
            try:
                await self._pubsub.aclose()
            except Exception:  # pragma: no cover
                pass
        for client in (self._client, self._observer):
            if client is not None:
                try:
                    await client.aclose()
                except Exception:  # pragma: no cover
                    pass
        self._client = self._observer = self._pubsub = None
        self._streams.clear()


_pubsub: RealtimePubSub | None = None


def get_pubsub() -> RealtimePubSub:
    global _pubsub
    if _pubsub is None:
        _pubsub = RealtimePubSub(get_settings().backend_cache_redis_uri)
    return _pubsub


async def close_pubsub() -> None:
    global _pubsub
    if _pubsub is not None:
        await _pubsub.close()
        _pubsub = None
