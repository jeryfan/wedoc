"""Auth stack: access-token (PAT) encryption, JWT facade, and the FastAPI
equivalent of the upstream AuthGuard strategy chain
(session -> access-token -> jwt -> anonymous).
"""

import base64
import json
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import jwt as pyjwt
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fastapi import Request
from sqlalchemy import column, select, table

from ...compat import pat_prefix
from ...config import Settings, get_settings
from ...db import engine as db_engine
from .. import cls
from ..cache import CacheService, get_cache, ms
from ..errors import ApiError, HttpErrorCode
from .constants import (
    ANONYMOUS_USER,
    APP_ROBOT_USER,
    AUTOMATION_ROBOT_USER,
    is_anonymous,
)
from .session import SessionHandle, SessionStore


class JwtAuthInternalType(StrEnum):
    AUTOMATION = "automation"
    APP = "app"
    USER = "user"


# ---------------------------------------------------------------------------
# encryptor (port of utils/encryptor.ts; AES-CBC over JSON, base64 wire form)
# ---------------------------------------------------------------------------


class Encryptor:
    """entries[0] encrypts; every entry participates in decryption, in order."""

    def __init__(
        self, entries: list[tuple[str, bytes, bytes]], encoding: str = "base64"
    ) -> None:
        if not entries:
            raise ValueError("Encryptor requires at least one cipher entry")
        self._entries = entries
        self._encoding = encoding

    def encrypt(self, data: Any) -> str:
        algorithm, key, iv = self._entries[0]
        encryptor = Cipher(_algorithm(algorithm, key), modes.CBC(iv)).encryptor()
        plaintext = json.dumps(data, separators=(",", ":")).encode()
        padded = _pkcs7_pad(plaintext)
        ciphertext = encryptor.update(padded) + encryptor.finalize()
        return self._encode(ciphertext)

    def decrypt(self, encrypted_data: str) -> Any:
        for algorithm, key, iv in self._entries:
            try:
                decryptor = Cipher(_algorithm(algorithm, key), modes.CBC(iv)).decryptor()
                padded = decryptor.update(self._decode(encrypted_data)) + decryptor.finalize()
                return json.loads(_pkcs7_unpad(padded))
            except Exception:
                # Wrong entry for this ciphertext — try the next one.
                continue
        raise ValueError("Decryption failed")

    def _encode(self, raw: bytes) -> str:
        if self._encoding == "base64":
            return base64.b64encode(raw).decode()
        return raw.hex()

    def _decode(self, data: str) -> bytes:
        if self._encoding == "base64":
            return base64.b64decode(data)
        return bytes.fromhex(data)


def _algorithm(name: str, key: bytes) -> algorithms.AES:
    if not name.startswith("aes-"):
        raise ValueError(f"unsupported cipher algorithm: {name}")
    return algorithms.AES(key)


def _pkcs7_pad(data: bytes, block_size: int = 16) -> bytes:
    pad_len = block_size - len(data) % block_size
    return data + bytes([pad_len]) * pad_len


def _pkcs7_unpad(data: bytes) -> bytes:
    if not data:
        raise ValueError("invalid padding")
    pad_len = data[-1]
    if pad_len < 1 or pad_len > 16 or data[-pad_len:] != bytes([pad_len]) * pad_len:
        raise ValueError("invalid padding")
    return data[:-pad_len]


# ---------------------------------------------------------------------------
# personal access token (port of access-token.encryptor.ts)
# ---------------------------------------------------------------------------


def _pat_cipher_entries(settings: Settings) -> list[tuple[str, bytes, bytes]]:
    """(algorithm, key, iv) triples: entries[0] encrypts, every entry decrypts.

    The _OLD pair is the decrypt-only tail pinned during a planned rotation;
    setting only half of it would strand old ciphertext, so it fails loudly.
    """
    algorithm = settings.backend_access_token_encryption_algorithm
    key, iv = settings.access_token_cipher
    entries = [(algorithm, key.encode(), iv.encode())]
    old_key = settings.backend_access_token_encryption_key_old
    old_iv = settings.backend_access_token_encryption_iv_old
    if bool(old_key) != bool(old_iv):
        raise ValueError(
            "BACKEND_ACCESS_TOKEN_ENCRYPTION_KEY_OLD and "
            "BACKEND_ACCESS_TOKEN_ENCRYPTION_IV_OLD must be set together"
        )
    if old_key and old_iv:
        old_entry = (algorithm, old_key.encode(), old_iv.encode())
        if old_entry != entries[0]:
            entries.append(old_entry)
    return entries


def _pat_encryptor(settings: Settings | None = None) -> Encryptor:
    settings = settings or get_settings()
    return Encryptor(_pat_cipher_entries(settings), encoding="base64")


def get_access_token(access_token_id: str, sign: str, settings: Settings | None = None) -> str:
    encrypted = _pat_encryptor(settings).encrypt({"sign": sign})
    return f"{pat_prefix()}_{access_token_id}_{encrypted}"


def split_access_token(
    access_token: str, settings: Settings | None = None
) -> dict[str, str] | None:
    parts = access_token.split("_")
    prefix = parts[0] if len(parts) > 0 else ""
    access_token_id = parts[1] if len(parts) > 1 else ""
    encrypted_sign = parts[2] if len(parts) > 2 else ""
    if not access_token_id:
        return None
    if prefix != pat_prefix():
        return None
    sign: str | None = None
    try:
        sign = _pat_encryptor(settings).decrypt(encrypted_sign)["sign"]
    except Exception:
        return None
    if not sign:
        return None
    return {"prefix": prefix, "accessTokenId": access_token_id, "sign": sign}


# ---------------------------------------------------------------------------
# JWT facade (port of the upstream jwt service: HS256, index 0 signs, all verify)
# ---------------------------------------------------------------------------


class JwtService:
    def __init__(
        self, secrets: list[str] | None = None, default_expires_in: str | None = None
    ) -> None:
        self._secrets = secrets
        self._default_expires_in = default_expires_in

    @property
    def secrets(self) -> list[str]:
        if self._secrets is None:
            self._secrets = get_settings().jwt_secrets
        return self._secrets

    @property
    def default_expires_in(self) -> str:
        if self._default_expires_in is None:
            self._default_expires_in = get_settings().backend_jwt_expires_in
        return self._default_expires_in

    def sign(
        self, payload: dict[str, Any], expires_in: str | int | None = None
    ) -> str:
        claims = dict(payload)
        now = int(time.time())
        claims.setdefault("iat", now)
        ttl = expires_in if expires_in is not None else self.default_expires_in
        if "exp" not in claims and ttl is not None:
            claims["exp"] = now + (ms(ttl) // 1000 if isinstance(ttl, str) else int(ttl))
        return pyjwt.encode(claims, self.secrets[0], algorithm="HS256")

    def verify(self, token: str) -> dict[str, Any]:
        last_error: Exception | None = None
        for secret in self.secrets:
            try:
                return pyjwt.decode(token, secret, algorithms=["HS256"])
            except (pyjwt.ExpiredSignatureError, pyjwt.ImmatureSignatureError):
                # The signature DID match this secret; older secrets cannot make
                # such a token valid — surface it as-is.
                raise
            except pyjwt.PyJWTError as error:
                last_error = error
        raise last_error or pyjwt.InvalidTokenError("invalid token")

    def classify_signing_secret(self, token: str) -> str:
        """'current' | 'old' | 'none', ignoring expiry (rotation tooling)."""
        for index, secret in enumerate(self.secrets):
            try:
                pyjwt.decode(
                    token, secret, algorithms=["HS256"], options={"verify_exp": False}
                )
                return "current" if index == 0 else "old"
            except pyjwt.PyJWTError:
                continue
        return "none"


# ---------------------------------------------------------------------------
# user loading (light reflection; wedoc.db.models_* is not ready yet)
# ---------------------------------------------------------------------------

_users = table(
    "users",
    column("id"),
    column("name"),
    column("email"),
    column("avatar"),
    column("phone"),
    column("password"),
    column("notify_meta"),
    column("is_admin"),
    column("lang"),
    column("deactivated_time"),
    column("is_system"),
    column("deleted_time"),
)

UserRecord = dict[str, Any]
UserLoader = Callable[[str], Awaitable[UserRecord | None]]


async def load_user_by_id(user_id: str) -> UserRecord | None:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(_users).where(_users.c.id == user_id, _users.c.deleted_time.is_(None))
            )
        ).mappings().first()
    if row is None:
        return None
    user = dict(row)
    notify_meta = user.get("notify_meta")
    if isinstance(notify_meta, str):
        user["notify_meta"] = json.loads(notify_meta)
    return user


def pick_user_me(user: UserRecord) -> dict[str, Any]:
    notify_meta = user.get("notify_meta")
    if notify_meta is not None and not isinstance(notify_meta, dict):
        notify_meta = json.loads(notify_meta)
    avatar = user.get("avatar")
    if avatar and not avatar.startswith("http"):
        from ..storage import get_public_full_storage_url

        avatar = get_public_full_storage_url(avatar)
    return {
        "id": user["id"],
        "name": user["name"],
        "phone": user.get("phone"),
        "email": user["email"],
        "isAdmin": user.get("is_admin"),
        "lang": user.get("lang"),
        "notifyMeta": notify_meta,
        "avatar": avatar,
        "hasPassword": user.get("password") is not None,
    }


# ---------------------------------------------------------------------------
# route metadata decorators (port of features/auth/decorators)
# ---------------------------------------------------------------------------


def _meta(func: Callable[..., Any], key: str, value: Any) -> Callable[..., Any]:
    markers = getattr(func, "__wedoc_security__", None)
    if markers is None:
        markers = {}
        func.__wedoc_security__ = markers
    markers[key] = value
    return func


def _get_meta(endpoint: Callable[..., Any] | None, key: str, default: Any = None) -> Any:
    if endpoint is None:
        return default
    return getattr(endpoint, "__wedoc_security__", {}).get(key, default)


def public() -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        return _meta(func, "isPublic", True)

    return decorator


class AllowAnonymousType(StrEnum):
    RESOURCE = "resource"
    USER = "user"
    PUBLIC = "public"


def allow_anonymous(
    type_: AllowAnonymousType = AllowAnonymousType.RESOURCE,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        return _meta(func, "isAllowAnonymous", type_)

    return decorator


def ensure_login() -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        return _meta(func, "ensureLogin", True)

    return decorator


def token_access() -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        return _meta(func, "isTokenAccess", True)

    return decorator


def permissions(*actions: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        return _meta(func, "permissions", list(actions))

    return decorator


def any_permissions(*groups: list[str]) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        return _meta(func, "anyPermissions", [list(group) for group in groups])

    return decorator


def resource_meta(
    type_: str, position: str
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """type_: 'spaceId' | 'baseId' | 'tableId'; position: 'query' | 'params' | 'body'."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        return _meta(func, "resourceMeta", {"type": type_, "position": position})

    return decorator


def disabled_permission() -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        return _meta(func, "isDisabledPermission", True)

    return decorator


def route_endpoint(request: Request) -> Callable[..., Any] | None:
    route = request.scope.get("route")
    return getattr(route, "endpoint", None)


class EnsureLoginRedirect(Exception):
    """Raised by the auth guard when @EnsureLogin is set and auth failed."""

    def __init__(self, redirect_url: str) -> None:
        super().__init__(redirect_url)
        self.redirect_url = redirect_url


# ---------------------------------------------------------------------------
# strategies (port of features/auth/strategies)
# ---------------------------------------------------------------------------


class _StrategyFail(Exception):
    """Passport-style fail(): try the next strategy."""


class _AccessTokenValidator:
    """AccessTokenService.validate port (sign check + expiry + lastUsedTime)."""

    LAST_USED_UPDATE_INTERVAL_MS = 5 * 60 * 1000

    _access_tokens = table(
        "access_token",
        column("id"),
        column("user_id"),
        column("sign"),
        column("expired_time"),
        column("last_used_time"),
    )

    async def validate(self, access_token_id: str, sign: str) -> tuple[str, str]:
        async with db_engine.session() as session:
            row = (
                await session.execute(
                    select(self._access_tokens).where(self._access_tokens.c.id == access_token_id)
                )
            ).mappings().first()
            if row is None:
                raise ApiError("token not found", HttpErrorCode.UNAUTHORIZED)
            if sign != row["sign"]:
                raise ApiError("sign error", HttpErrorCode.UNAUTHORIZED)
            expired_time = row["expired_time"]
            # expiredTime 1s tolerance (upstream: Date.now() + 1000)
            if (
                expired_time is not None
                and expired_time.timestamp() * 1000 < time.time() * 1000 + 1000
            ):
                raise ApiError("token expired", HttpErrorCode.UNAUTHORIZED)
            now_ms = time.time() * 1000
            last_used = row["last_used_time"]
            if (
                last_used is None
                or now_ms - last_used.timestamp() * 1000 >= self.LAST_USED_UPDATE_INTERVAL_MS
            ):
                await session.execute(
                    self._access_tokens.update()
                    .where(self._access_tokens.c.id == access_token_id)
                    .values(last_used_time=datetime.now(tz=UTC))
                )
                await session.commit()
        return str(row["user_id"]), str(row["id"])


class AuthGuard:
    """Passport chain port: session -> access-token -> auth-jwt-token -> anonymous."""

    def __init__(
        self,
        session_handle: SessionHandle | None = None,
        user_loader: UserLoader = load_user_by_id,
        jwt_service: JwtService | None = None,
        access_token_validator: _AccessTokenValidator | None = None,
    ) -> None:
        if session_handle is None:
            cache: CacheService = get_cache()
            session_handle = SessionHandle(SessionStore(cache))
        self._session_handle = session_handle
        self._user_loader = user_loader
        self._jwt = jwt_service or JwtService()
        self._access_token_validator = access_token_validator or _AccessTokenValidator()

    async def authorize(self, request: Request) -> dict[str, Any]:
        endpoint = route_endpoint(request)
        if _get_meta(endpoint, "isPublic"):
            return {}
        try:
            user = await self._run_strategies(request)
            allow_anonymous_type = _get_meta(endpoint, "isAllowAnonymous")
            if not allow_anonymous_type and is_anonymous(user.get("id")):
                raise ApiError("Unauthorized", HttpErrorCode.UNAUTHORIZED)
            return user
        except Exception as error:
            if _get_meta(endpoint, "ensureLogin"):
                from urllib.parse import quote

                url = request.url.path
                if request.url.query:
                    url += "?" + request.url.query
                raise EnsureLoginRedirect(f"/auth/signup?redirect={quote(url, safe='')}") from error
            raise

    async def _run_strategies(self, request: Request) -> dict[str, Any]:
        for strategy in (
            self._session_strategy,
            self._access_token_strategy,
            self._jwt_strategy,
        ):
            try:
                return await strategy(request)
            except _StrategyFail:
                continue
        return self._anonymous_strategy()

    # -- session ------------------------------------------------------------
    async def _session_strategy(self, request: Request) -> dict[str, Any]:
        sid = self._session_handle.session_id_from_cookie_header(
            request.headers.get("cookie")
        )
        if not sid:
            raise _StrategyFail("no session cookie")
        session = await self._session_handle.store.get(sid)
        payload_user = (session or {}).get("passport", {}).get("user")
        if not payload_user:
            raise _StrategyFail("no user in session")
        user = await self._user_loader(payload_user["id"])
        if not user:
            raise ApiError("Unauthorized", HttpErrorCode.UNAUTHORIZED)
        if user.get("deactivated_time"):
            raise ApiError(
                "Your account has been deactivated by the administrator",
                HttpErrorCode.UNAUTHORIZED,
            )
        if user.get("is_system"):
            raise ApiError("User is system user", HttpErrorCode.UNAUTHORIZED)
        self._set_cls_user(user)
        return pick_user_me(user)

    # -- personal access token ----------------------------------------------
    async def _access_token_strategy(self, request: Request) -> dict[str, Any]:
        token = _bearer_token(request)
        if not token:
            raise _StrategyFail("no bearer token")
        token_obj = split_access_token(token)
        if not token_obj:
            raise _StrategyFail("invalid access token")
        user_id, access_token_id = await self._access_token_validator.validate(
            token_obj["accessTokenId"], token_obj["sign"]
        )
        user = await self._user_loader(user_id)
        if not user:
            raise ApiError("User not found", HttpErrorCode.UNAUTHORIZED)
        if user.get("deactivated_time"):
            raise ApiError(
                "Your account has been deactivated by the administrator",
                HttpErrorCode.UNAUTHORIZED,
            )
        self._set_cls_user(user)
        cls.set("accessTokenId", access_token_id)
        return pick_user_me(user)

    # -- jwt ------------------------------------------------------------------
    async def _jwt_strategy(self, request: Request) -> dict[str, Any]:
        token = _bearer_token(request)
        if not token:
            raise _StrategyFail("no bearer token")
        try:
            payload = self._jwt.verify(token)
        except pyjwt.PyJWTError as error:
            raise _StrategyFail(str(error)) from error
        if "baseId" in payload:
            return await self._validate_internal_token(payload)
        return await self._validate_user_token(payload)

    async def _validate_internal_token(self, payload: dict[str, Any]) -> dict[str, Any]:
        cls.set("tempAuthBaseId", payload["baseId"])
        if payload.get("type") == JwtAuthInternalType.USER:
            user_id = payload.get("userId")
            if not user_id:
                raise ApiError(
                    "User ID is required for User type tokens", HttpErrorCode.UNAUTHORIZED
                )
            user = await self._user_loader(user_id)
            if not user:
                raise ApiError("Unauthorized", HttpErrorCode.UNAUTHORIZED)
            if user.get("deactivated_time"):
                raise ApiError(
                    "Your account has been deactivated by the administrator",
                    HttpErrorCode.UNAUTHORIZED,
                )
            if user.get("is_system"):
                raise ApiError("User is system user", HttpErrorCode.UNAUTHORIZED)
            self._set_cls_user(user)
            return pick_user_me(user)
        user = (
            APP_ROBOT_USER
            if payload.get("type") == JwtAuthInternalType.APP
            else AUTOMATION_ROBOT_USER
        )
        cls.set("user", dict(user))
        cls.set("tempAuthBaseId", payload["baseId"])
        if payload.get("type") == JwtAuthInternalType.AUTOMATION:
            cls.set("workflowContext", payload.get("context"))
        return dict(user)

    async def _validate_user_token(self, payload: dict[str, Any]) -> dict[str, Any]:
        user = await self._user_loader(payload["userId"])
        if not user:
            raise ApiError("Unauthorized", HttpErrorCode.UNAUTHORIZED)
        if user.get("deactivated_time"):
            raise ApiError(
                "Your account has been deactivated by the administrator",
                HttpErrorCode.UNAUTHORIZED,
            )
        if user.get("is_system") and payload.get("allowSystemUser") is not True:
            raise ApiError("User is system user", HttpErrorCode.UNAUTHORIZED)
        self._set_cls_user(user)
        return pick_user_me(user)

    # -- anonymous --------------------------------------------------------------
    def _anonymous_strategy(self) -> dict[str, Any]:
        cls.set("user", dict(ANONYMOUS_USER))
        return dict(ANONYMOUS_USER)

    @staticmethod
    def _set_cls_user(user: UserRecord) -> None:
        cls.set("user.id", user["id"])
        cls.set("user.name", user["name"])
        cls.set("user.email", user["email"])
        cls.set("user.isAdmin", user.get("is_admin"))


def _bearer_token(request: Request) -> str | None:
    auth_header = request.headers.get("authorization")
    if auth_header:
        bearer, _, token = auth_header.partition(" ")
        if bearer == "Bearer" and token:
            return token
    return None


async def auth_guard(request: Request) -> dict[str, Any]:
    """FastAPI dependency: the global AuthGuard."""
    return await AuthGuard().authorize(request)
