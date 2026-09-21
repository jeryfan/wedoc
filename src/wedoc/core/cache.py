"""keyv-compatible cache layer over redis.asyncio.

Wire format mirrors the upstream keyv v4 + @keyv/redis (useRedisSets: false)
stack byte-for-byte so a swapped-in deployment reads and writes the same keys:

- physical key: ``{namespace}:{key}`` (namespace from ``wedoc.compat``)
- value envelope: JSON ``{"value": <payload>, "expires": <epoch ms> | null}``
- when a TTL is given, the Redis key also carries a PX expiry matching the
  envelope's ``expires``
"""

import json
import random
import time
from typing import Any

import redis.asyncio as aioredis
import structlog

from ..compat import cache_key_namespace
from ..config import get_settings
from .duration import parse_ms

logger = structlog.get_logger(__name__)


def ms(value: str | int | float) -> int:
    return parse_ms(value)


def second(value: str) -> int:
    return parse_ms(value) // 1000


def serialize_envelope(value: Any, expires: float | None) -> str:
    return json.dumps({"value": value, "expires": expires}, separators=(",", ":"))


def _now_ms() -> float:
    return time.time() * 1000


class CacheService:
    """Drop-in equivalent of the upstream CacheService (keyv facade)."""

    def __init__(self, redis: aioredis.Redis, namespace: str | None = None) -> None:
        self._redis = redis
        self.namespace = namespace if namespace is not None else cache_key_namespace()

    def physical_key(self, key: str) -> str:
        return f"{self.namespace}:{key}"

    async def get(self, key: str) -> Any | None:
        raw = await self._redis.get(self.physical_key(key))
        if raw is None:
            return None
        envelope = json.loads(raw)
        expires = envelope.get("expires")
        if expires is not None and expires <= _now_ms():
            await self._redis.delete(self.physical_key(key))
            return None
        return envelope.get("value")

    async def get_many(self, keys: list[str]) -> list[Any | None]:
        # Redis MGET rejects an empty key list, like the upstream store path
        if not keys:
            return []
        raw_values = await self._redis.mget([self.physical_key(key) for key in keys])
        result: list[Any | None] = []
        for key, raw in zip(keys, raw_values, strict=True):
            if raw is None:
                result.append(None)
                continue
            envelope = json.loads(raw)
            expires = envelope.get("expires")
            if expires is not None and expires <= _now_ms():
                await self._redis.delete(self.physical_key(key))
                result.append(None)
                continue
            result.append(envelope.get("value"))
        return result

    async def set(self, key: str, value: Any, ttl: int | str | None = None) -> None:
        """ttl in seconds (or ms-syntax string); adds a random 20-60s on top."""
        ttl_seconds = second(ttl) if isinstance(ttl, str) else ttl
        self._warn_not_set_ttl(key, ttl_seconds)
        effective = (ttl_seconds + random.randint(20, 60)) if ttl_seconds else None
        await self._set_envelope(key, value, effective)

    async def set_detail(self, key: str, value: Any, ttl: int | str | None = None) -> None:
        """set() without the random TTL jitter."""
        ttl_seconds = second(ttl) if isinstance(ttl, str) else ttl
        self._warn_not_set_ttl(key, ttl_seconds)
        await self._set_envelope(key, value, ttl_seconds or None)

    async def _set_envelope(self, key: str, value: Any, ttl_seconds: int | None) -> None:
        expires = _now_ms() + ttl_seconds * 1000 if ttl_seconds else None
        payload = serialize_envelope(value, expires)
        if ttl_seconds:
            await self._redis.set(self.physical_key(key), payload, px=ttl_seconds * 1000)
        else:
            await self._redis.set(self.physical_key(key), payload)

    async def delete(self, key: str) -> bool:
        """True if the key existed, so callers can use it as an atomic consume."""
        return await self._redis.delete(self.physical_key(key)) > 0

    async def clear(self) -> None:
        pattern = f"{self.namespace}:*"
        async for key in self._redis.scan_iter(match=pattern, count=500):
            await self._redis.delete(key)

    async def setnx(self, key: str, value: Any, ttl_seconds: int) -> bool:
        """Atomic SET NX EX on the same physical key and envelope as set()."""
        payload = serialize_envelope(value, _now_ms() + ttl_seconds * 1000)
        result = await self._redis.set(
            self.physical_key(key), payload, px=ttl_seconds * 1000, nx=True
        )
        return result is not None

    async def incr(self, key: str, ttl_seconds: int | None = None) -> int:
        """Bare-integer counter (no envelope; not readable via get())."""
        full_key = self.physical_key(key)
        new_value = await self._redis.incr(full_key)
        if ttl_seconds and new_value == 1:
            await self._redis.expire(full_key, ttl_seconds)
        return new_value

    async def expire(self, key: str, ttl: int | str) -> bool:
        """Update Redis TTL only; safe for shortening envelope-carrying keys."""
        ttl_seconds = second(ttl) if isinstance(ttl, str) else ttl
        return bool(await self._redis.expire(self.physical_key(key), ttl_seconds))

    def _warn_not_set_ttl(self, key: str, ttl: int | None) -> None:
        if not ttl or ttl <= 0:
            logger.warning("cache set without ttl", key=key)

    async def close(self) -> None:
        await self._redis.aclose()


_cache: CacheService | None = None


def get_cache() -> CacheService:
    global _cache
    if _cache is None:
        settings = get_settings()
        _cache = CacheService(aioredis.from_url(settings.backend_cache_redis_uri))
    return _cache


async def close_cache() -> None:
    global _cache
    if _cache is not None:
        await _cache.close()
        _cache = None
