"""express-session compatible session layer.

Wire compatibility contract (existing Redis entries and issued cookies must
keep working after the swap):

- cookie name ``auth_session`` (upstream ``AUTH_SESSION_COOKIE_NAME``)
- cookie value ``s:<sid>.<sig>`` where ``sig`` is
  ``base64(hmac-sha256(sid, secret))`` with trailing ``=`` stripped, then the
  whole value is percent-encoded (encodeURIComponent) on the wire
- secret array rotation: index 0 signs new cookies, every secret verifies
- sid: uid-safe compatible — 24 random bytes, base64url without padding
  (32 chars)
- session JSON stored under ``auth:session-store:{sid}`` (through the keyv
  envelope of ``wedoc.core.cache``), shape ``{"cookie": {...},
  "passport": {"user": {"id": ...}}}``
- per-user session index ``auth:session-user:{userId}`` mapping
  ``sid -> expiry epoch seconds`` (session ttl + 120s), plus the
  ``auth:session-expire:{sid}`` tombstone and ``auth:session-user-cleared:{userId}``
  revocation marker, with the same repair/expiry semantics as the upstream
  SessionStoreService
"""

import base64
import hashlib
import hmac
import re
import secrets as secrets_mod
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, unquote

from ...config import get_settings
from ..cache import CacheService, second

AUTH_SESSION_COOKIE_NAME = "auth_session"

# express-session cookie options: maxAge '1y', httpOnly, sameSite 'lax'
COOKIE_MAX_AGE_MS = 31_536_000_000
COOKIE_SAME_SITE = "lax"
COOKIE_PATH = "/"

# JS encodeURIComponent leaves these unescaped (alphanumerics are never escaped)
_URI_SAFE = "!'()*-._~"
_B64_PAD = re.compile(r"=+$")


def generate_sid() -> str:
    """uid-safe sync(24): 24 random bytes as unpadded base64url (32 chars)."""
    return base64.urlsafe_b64encode(secrets_mod.token_bytes(24)).decode().rstrip("=")


def _signature(sid: str, secret: str) -> str:
    digest = hmac.new(secret.encode(), sid.encode(), hashlib.sha256).digest()
    return _B64_PAD.sub("", base64.b64encode(digest).decode())


def sign_sid(sid: str, secret: str) -> str:
    """cookie-signature sign(): ``s:<sid>.<sig>`` (unencoded form)."""
    return f"s:{sid}.{_signature(sid, secret)}"


def encode_cookie_value(signed: str) -> str:
    """The on-the-wire form: percent-encoded like cookie.serialize defaults."""
    return quote(signed, safe=_URI_SAFE)


def unsign_cookie_value(value: str, secrets: list[str]) -> str | None:
    """cookie-signature unsign() against every secret, in order.

    Accepts either the raw or the percent-encoded form; returns the sid.
    """
    if "%" in value:
        try:
            value = unquote(value, errors="strict")
        except Exception:
            return None
    if not value.startswith("s:"):
        return None
    signed = value[2:]
    sid, sep, _sig = signed.rpartition(".")
    if not sep or not sid:
        return None
    for secret in secrets:
        expected = f"{sid}.{_signature(sid, secret)}"
        if hmac.compare_digest(
            hashlib.sha1(expected.encode()).digest(),
            hashlib.sha1(signed.encode()).digest(),
        ):
            return sid
    return None


def parse_cookie_header(header: str | None) -> dict[str, str]:
    """First occurrence wins, like express's cookie parser."""
    cookies: dict[str, str] = {}
    if not header:
        return cookies
    for part in header.split(";"):
        name, sep, val = part.partition("=")
        if not sep:
            continue
        name = name.strip()
        if name and name not in cookies:
            cookies[name] = val.strip()
    return cookies


def session_id_from_cookies(cookies: dict[str, str], secrets: list[str]) -> str | None:
    raw = cookies.get(AUTH_SESSION_COOKIE_NAME)
    if raw is None:
        return None
    return unsign_cookie_value(raw, secrets)


def build_session_cookie(secure: bool | None = None) -> dict[str, Any]:
    """The ``cookie`` sub-object of a fresh session, as express-session builds it."""
    expires = datetime.fromtimestamp((time.time() * 1000 + COOKIE_MAX_AGE_MS) / 1000, tz=UTC)
    cookie: dict[str, Any] = {
        "originalMaxAge": COOKIE_MAX_AGE_MS,
        "expires": expires.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "httpOnly": True,
        "path": COOKIE_PATH,
        "sameSite": COOKIE_SAME_SITE,
    }
    if secure is not None:
        cookie["secure"] = secure
    return cookie


def session_cookie_secure() -> bool | str | None:
    """Upstream getCookieSecure: unset → None, 'auto' → 'auto', else boolean."""
    value = get_settings().backend_session_cookie_secure
    if not value:
        return None
    if value == "auto":
        return "auto"
    return value == "true"


def new_session_data(user_id: str, secure: bool | None = None) -> dict[str, Any]:
    return {
        "cookie": build_session_cookie(secure),
        "passport": {"user": {"id": user_id}},
    }


def build_set_cookie_header(
    sid: str, secrets: list[str] | None = None, secure: bool | None = None
) -> str:
    """The express-session Set-Cookie for a freshly saved session."""
    from email.utils import formatdate

    secrets = secrets if secrets is not None else get_settings().session_secrets
    value = encode_cookie_value(sign_sid(sid, secrets[0]))
    expires = formatdate(time.time() + COOKIE_MAX_AGE_MS / 1000, usegmt=True)
    parts = [
        f"{AUTH_SESSION_COOKIE_NAME}={value}",
        f"Path={COOKIE_PATH}",
        f"Expires={expires}",
        "HttpOnly",
        "SameSite=Lax",
    ]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def build_clear_cookie_header() -> str:
    """express res.clearCookie(name): empty value, epoch expiry, default path."""
    return f"{AUTH_SESSION_COOKIE_NAME}=; Path={COOKIE_PATH}; Expires=Thu, 01 Jan 1970 00:00:00 GMT"


def _session_renewed_at_sec(session: dict[str, Any], ttl: int) -> int:
    """cookie.expires is stamped now+ttl on save and every rolling touch.

    Unknown expiry is treated as renewed "now" so a fresh post-clear session is
    never mistaken for a revoked one.
    """
    expires = (session.get("cookie") or {}).get("expires")
    expires_ms: float = float("nan")
    if isinstance(expires, str):
        try:
            expires_ms = (
                datetime.fromisoformat(expires.replace("Z", "+00:00")).timestamp() * 1000
            )
        except ValueError:
            expires_ms = float("nan")
    if expires_ms != expires_ms:  # NaN
        return int(time.time())
    return int(expires_ms // 1000) - ttl


class SessionStore:
    """Python port of the upstream SessionStoreService (same keys, same semantics)."""

    def __init__(self, cache: CacheService, session_expires_in: str | None = None) -> None:
        expires_in = session_expires_in or get_settings().backend_session_expires_in
        self.ttl = second(expires_in)
        # Greater than the session cache time, so the user-session index does
        # not expire while the session is still alive.
        self.user_session_expire = self.ttl + 60 * 2
        self._cache = cache

    async def _set_cache(self, sid: str, session: dict[str, Any]) -> None:
        user_id = session["passport"]["user"]["id"]
        user_sessions: dict[str, int] = (
            await self._cache.get(f"auth:session-user:{user_id}")
        ) or {}
        now_sec = int(time.time())
        user_sessions[sid] = now_sec + self.user_session_expire
        # Maintain userSessions, remove expired keys
        for key, value in list(user_sessions.items()):
            if value < now_sec:
                del user_sessions[key]
        await self._cache.set(f"auth:session-user:{user_id}", user_sessions, self.ttl)
        await self._cache.set(f"auth:session-store:{sid}", session, self.ttl)

    async def _get_cache(self, sid: str) -> dict[str, Any] | None:
        expire = await self._cache.get(f"auth:session-expire:{sid}")
        if expire:
            return None
        session: dict[str, Any] | None = await self._cache.get(f"auth:session-store:{sid}")
        if not session:
            return None
        user_id = session["passport"]["user"]["id"]
        user_sessions: dict[str, int] = (
            await self._cache.get(f"auth:session-user:{user_id}")
        ) or {}
        if sid not in user_sessions:
            # A missing entry only means "revoked" when a clear_by_user_id
            # actually happened and this session predates it; otherwise repair
            # the map instead of destroying a session the user still holds.
            cleared_at_sec = await self._cache.get(f"auth:session-user-cleared:{user_id}")
            if cleared_at_sec and _session_renewed_at_sec(session, self.ttl) <= cleared_at_sec:
                await self._cache.delete(f"auth:session-store:{sid}")
                return None
            user_sessions[sid] = int(time.time()) + self.user_session_expire
            await self._cache.set(f"auth:session-user:{user_id}", user_sessions, self.ttl)
            return session
        now_sec = int(time.time())
        if user_sessions[sid] < now_sec:
            del user_sessions[sid]
            await self._cache.delete(f"auth:session-store:{sid}")
            await self._cache.set(f"auth:session-user:{user_id}", user_sessions, self.ttl)
            return None
        return session

    async def get(self, sid: str) -> dict[str, Any] | None:
        return await self._get_cache(sid)

    async def set(self, sid: str, session: dict[str, Any]) -> None:
        # Only passport + cookie are persisted, like the upstream pick()
        await self._set_cache(sid, {k: session[k] for k in ("passport", "cookie") if k in session})

    async def destroy(self, sid: str) -> None:
        await self._cache.delete(f"auth:session-store:{sid}")

    async def touch(self, sid: str, session: dict[str, Any]) -> None:
        cached = await self._get_cache(sid)
        if cached is None:
            raise ValueError("Session not found")
        await self._set_cache(sid, session)

    async def clear_by_user_id(self, user_id: str) -> None:
        # Mark the clear before deleting anything so the _get_cache repair path
        # cannot resurrect the sessions being revoked.
        await self._cache.set(
            f"auth:session-user-cleared:{user_id}", int(time.time()), self.user_session_expire
        )
        user_sessions: dict[str, int] = (
            await self._cache.get(f"auth:session-user:{user_id}")
        ) or {}
        for sid in user_sessions:
            # Preventing competition
            await self._cache.set(f"auth:session-expire:{sid}", True, 60)
            await self._cache.delete(f"auth:session-store:{sid}")
        await self._cache.delete(f"auth:session-user:{user_id}")

    async def get_user_id(self, sid: str) -> str | None:
        session = await self.get(sid)
        if not session:
            return None
        return session["passport"]["user"]["id"]


class SessionHandle:
    """Parses the session cookie of an incoming request (SessionHandleService port)."""

    def __init__(self, store: SessionStore, secrets: list[str] | None = None) -> None:
        self.store = store
        self.secrets = secrets if secrets is not None else get_settings().session_secrets

    def session_id_from_cookie_header(self, cookie_header: str | None) -> str | None:
        return session_id_from_cookies(parse_cookie_header(cookie_header), self.secrets)

    async def get_user_id(self, session_id: str) -> str | None:
        return await self.store.get_user_id(session_id)
