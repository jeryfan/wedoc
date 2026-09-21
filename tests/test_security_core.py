"""Contract tests for the cache/session/auth/permission core layers.

Redis-backed tests use the local dev instance (redis://default:wedoc@127.0.0.1:46380)
and are skipped when it is unreachable.
"""

import base64
import json
import os
import time

import pytest
import redis.asyncio as aioredis

from wedoc.compat import cache_key_namespace, pat_prefix
from wedoc.core import cls as wedoc_cls
from wedoc.core.cache import CacheService, ms, second
from wedoc.core.security.auth import (
    Encryptor,
    JwtService,
    get_access_token,
    split_access_token,
)
from wedoc.core.security.constants import ANONYMOUS_USER_ID, is_anonymous
from wedoc.core.security.permissions import (
    TEMPLATE_PERMISSIONS,
    Role,
    check_permissions,
    get_max_level_role,
    get_permissions,
    has_permission,
    is_restricted_role,
)
from wedoc.core.security.session import (
    SessionStore,
    encode_cookie_value,
    generate_sid,
    new_session_data,
    parse_cookie_header,
    session_id_from_cookies,
    sign_sid,
    unsign_cookie_value,
)

SESSION_SECRET = "dafea6be69af1c1c3b8caf2b609342f6eb4540b554e19539f7643b75b480c932"
SESSION_SECRET_OLD = "old-session-secret-value"
JWT_SECRET = "533Cr3tK3yF0rH4sh1nGJ4W773k3n$"
ACCESS_TOKEN_KEY = b"ie21hOKjlXUiGDx9"
ACCESS_TOKEN_IV = b"i0vKGXBWkzyAoGf4"

REDIS_URL = "redis://default:wedoc@127.0.0.1:46380"
# get_settings() is lazy; seeding env here is enough for the PAT/JWT defaults
os.environ.setdefault("PUBLIC_ORIGIN", "http://localhost:3000")
os.environ.setdefault("BACKEND_CACHE_REDIS_URI", REDIS_URL)


@pytest.fixture
async def redis_client():
    client = aioredis.from_url(REDIS_URL)
    try:
        await client.ping()
    except Exception:
        await client.aclose()
        pytest.skip("local redis unavailable")
    yield client
    await client.aclose()


@pytest.fixture
async def cache(redis_client):
    service = CacheService(redis_client)
    yield service
    async for key in redis_client.scan_iter(match=f"{service.namespace}:test:*"):
        await redis_client.delete(key)


# ---------------------------------------------------------------------------
# ms / second
# ---------------------------------------------------------------------------


def test_ms_parser():
    assert ms("7d") == 604_800_000
    assert ms("1y") == 31_536_000_000
    assert ms("20d") == 1_728_000_000
    assert ms("30m") == 1_800_000
    assert ms("10s") == 10_000
    assert ms(1000) == 1000
    assert second("7d") == 604_800


# ---------------------------------------------------------------------------
# session cookie signing (cookie-signature 1.0.6 / express-session 1.18)
# ---------------------------------------------------------------------------

# Produced with node + the upstream secret:
#   crypto.createHmac("sha256", secret).update(sid).digest("base64").replace(/=+$/, "")
UPSTREAM_SIGNED_COOKIE = (
    "s:abcdefghijklmnopqrstuvwxyz123456.gvXGK4kARXWfYE272LJ62cnYTuRdPWS0ljNyOar8LZA"
)
UPSTREAM_SIGNED_COOKIE_ENCODED = (
    "s%3Aabcdefghijklmnopqrstuvwxyz123456.gvXGK4kARXWfYE272LJ62cnYTuRdPWS0ljNyOar8LZA"
)
UPSTREAM_SID = "abcdefghijklmnopqrstuvwxyz123456"


def test_sign_sid_matches_upstream_fixture():
    assert sign_sid(UPSTREAM_SID, SESSION_SECRET) == UPSTREAM_SIGNED_COOKIE


def test_encode_cookie_value_matches_encode_uri_component():
    assert encode_cookie_value(UPSTREAM_SIGNED_COOKIE) == UPSTREAM_SIGNED_COOKIE_ENCODED


def test_unsign_accepts_raw_and_percent_encoded():
    assert unsign_cookie_value(UPSTREAM_SIGNED_COOKIE, [SESSION_SECRET]) == UPSTREAM_SID
    assert unsign_cookie_value(UPSTREAM_SIGNED_COOKIE_ENCODED, [SESSION_SECRET]) == UPSTREAM_SID


def test_unsign_secret_rotation():
    secrets = [SESSION_SECRET, SESSION_SECRET_OLD]
    # signed with the old secret; new secret first in the array
    assert unsign_cookie_value(sign_sid(UPSTREAM_SID, SESSION_SECRET_OLD), secrets) == UPSTREAM_SID
    # signed with the current secret still verifies
    assert unsign_cookie_value(sign_sid(UPSTREAM_SID, SESSION_SECRET), secrets) == UPSTREAM_SID


def test_unsign_rejects_tampered_values():
    forged = "s:" + UPSTREAM_SID + "." + "A" * 43
    assert unsign_cookie_value(forged, [SESSION_SECRET]) is None
    assert unsign_cookie_value(UPSTREAM_SIGNED_COOKIE + "x", [SESSION_SECRET]) is None
    assert unsign_cookie_value("not-signed", [SESSION_SECRET]) is None
    assert unsign_cookie_value("", [SESSION_SECRET]) is None
    other_sid = "b" * 32
    assert unsign_cookie_value(
        UPSTREAM_SIGNED_COOKIE.replace(UPSTREAM_SID, other_sid), [SESSION_SECRET]
    ) is None


def test_generate_sid_uid_safe_compatible():
    sid = generate_sid()
    assert len(sid) == 32
    assert all(c.isalnum() or c in "-_" for c in sid)


def test_parse_cookie_header_first_wins():
    cookies = parse_cookie_header("auth_session=a; other=1; auth_session=b")
    assert cookies["auth_session"] == "a"
    assert cookies["other"] == "1"
    assert parse_cookie_header(None) == {}


def test_session_id_from_cookies():
    header = "foo=bar; auth_session=" + encode_cookie_value(UPSTREAM_SIGNED_COOKIE)
    cookies = parse_cookie_header(header)
    assert session_id_from_cookies(cookies, [SESSION_SECRET]) == UPSTREAM_SID


def test_new_session_data_shape():
    data = new_session_data("usrTest", secure=False)
    assert data["passport"] == {"user": {"id": "usrTest"}}
    cookie = data["cookie"]
    assert cookie["originalMaxAge"] == 31_536_000_000
    assert cookie["httpOnly"] is True
    assert cookie["path"] == "/"
    assert cookie["sameSite"] == "lax"
    assert cookie["secure"] is False
    assert cookie["expires"].endswith("Z")


# ---------------------------------------------------------------------------
# keyv-compatible cache envelope
# ---------------------------------------------------------------------------


async def test_cache_roundtrip_and_envelope_format(cache, redis_client):
    await cache.set_detail("test:plain", {"a": 1}, 60)
    raw = await redis_client.get(f"{cache_key_namespace()}:test:plain")
    envelope = json.loads(raw)
    assert envelope["value"] == {"a": 1}
    assert isinstance(envelope["expires"], int | float)
    assert envelope["expires"] > time.time() * 1000
    assert await cache.get("test:plain") == {"a": 1}


async def test_cache_reads_upstream_written_envelope(cache, redis_client):
    # Simulate a value written by the upstream keyv stack directly
    expires = int(time.time() * 1000) + 60_000
    await redis_client.set(
        f"{cache_key_namespace()}:test:upstream",
        json.dumps({"value": {"passport": {"user": {"id": "usrX"}}}, "expires": expires}),
    )
    assert await cache.get("test:upstream") == {"passport": {"user": {"id": "usrX"}}}


async def test_cache_expired_envelope_is_invisible(cache, redis_client):
    await redis_client.set(
        f"{cache_key_namespace()}:test:expired",
        json.dumps({"value": 1, "expires": int(time.time() * 1000) - 1000}),
    )
    assert await cache.get("test:expired") is None


async def test_cache_delete_and_get_many_and_setnx(cache):
    await cache.set_detail("test:a", 1, 60)
    await cache.set_detail("test:b", 2, 60)
    assert await cache.get_many(["test:a", "test:b", "test:missing"]) == [1, 2, None]
    assert await cache.get_many([]) == []
    assert await cache.delete("test:a") is True
    assert await cache.delete("test:a") is False
    assert await cache.setnx("test:nx", "v", 30) is True
    assert await cache.setnx("test:nx", "v2", 30) is False
    assert await cache.get("test:nx") == "v"


async def test_cache_set_adds_jitter(cache, redis_client):
    await cache.set("test:jitter", 1, 100)
    ttl_ms = await redis_client.pttl(f"{cache_key_namespace()}:test:jitter")
    assert 100_000 <= ttl_ms <= 161_000


# ---------------------------------------------------------------------------
# session store (auth:session-* key family semantics)
# ---------------------------------------------------------------------------


async def test_session_store_set_get_destroy(cache, redis_client):
    store = SessionStore(cache, session_expires_in="7d")
    sid = generate_sid()
    data = new_session_data("usrStoreTest")
    await store.set(sid, data)
    assert await store.get(sid) == data

    # physical keys match the upstream layout
    assert await redis_client.exists(f"{cache_key_namespace()}:auth:session-store:{sid}")
    user_index = await cache.get("auth:session-user:usrStoreTest")
    assert sid in user_index
    assert user_index[sid] > int(time.time())

    assert await store.get_user_id(sid) == "usrStoreTest"
    await store.destroy(sid)
    assert await store.get(sid) is None
    await redis_client.delete(f"{cache_key_namespace()}:auth:session-user:usrStoreTest")


async def test_session_store_clear_by_user_id(cache, redis_client):
    store = SessionStore(cache, session_expires_in="7d")
    user_id = "usrClearTest"
    sid1, sid2 = generate_sid(), generate_sid()
    await store.set(sid1, new_session_data(user_id))
    await store.set(sid2, new_session_data(user_id))
    await store.clear_by_user_id(user_id)
    assert await store.get(sid1) is None
    assert await store.get(sid2) is None
    assert await cache.get(f"auth:session-user:{user_id}") is None
    # tombstone keys exist briefly to win races with concurrent reads
    assert await cache.get(f"auth:session-expire:{sid1}") is True
    assert await cache.get(f"auth:session-user-cleared:{user_id}") is not None
    for key in (
        f"auth:session-expire:{sid1}",
        f"auth:session-expire:{sid2}",
        f"auth:session-user-cleared:{user_id}",
    ):
        await cache.delete(key)


async def test_session_store_repairs_lost_user_index_entry(cache):
    store = SessionStore(cache, session_expires_in="7d")
    user_id = "usrRepairTest"
    sid = generate_sid()
    await store.set(sid, new_session_data(user_id))
    # Simulate a lost map update: drop the per-user index only
    await cache.delete(f"auth:session-user:{user_id}")
    session = await store.get(sid)
    assert session is not None
    assert session["passport"]["user"]["id"] == user_id
    restored = await cache.get(f"auth:session-user:{user_id}")
    assert sid in restored
    await store.destroy(sid)
    await cache.delete(f"auth:session-user:{user_id}")


async def test_session_store_revoked_after_clear_is_not_repaired(cache):
    store = SessionStore(cache, session_expires_in="7d")
    user_id = "usrRevokedTest"
    sid = generate_sid()
    session = new_session_data(user_id)
    # Backdate the renewal so the session predates the clear marker
    past = time.time() - 3600
    expires_ms = (past + store.ttl) * 1000
    from datetime import UTC, datetime

    session["cookie"]["expires"] = (
        datetime.fromtimestamp(expires_ms / 1000, tz=UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
    await cache.set(f"auth:session-store:{sid}", session, store.ttl)
    await cache.set(f"auth:session-user-cleared:{user_id}", int(time.time()), 60)
    assert await store.get(sid) is None
    assert await cache.get(f"auth:session-store:{sid}") is None
    await cache.delete(f"auth:session-user-cleared:{user_id}")


# ---------------------------------------------------------------------------
# personal access token (AES-128-CBC + prefix)
# ---------------------------------------------------------------------------

PAT_ENTRIES = [("aes-128-cbc", ACCESS_TOKEN_KEY, ACCESS_TOKEN_IV)]

# Produced with node: crypto.createCipheriv("aes-128-cbc", key, iv) over
# JSON.stringify({sign: "0123456789abcdef"}), base64 output.
UPSTREAM_PAT_SIGN = "0123456789abcdef"
UPSTREAM_PAT_CIPHERTEXT = "xTrKqKM7Ue3n6+LdFneCKNgEjeD9gx4cY52qJbCMyhw="


def test_encryptor_matches_upstream_ciphertext():
    encryptor = Encryptor(PAT_ENTRIES, encoding="base64")
    assert encryptor.encrypt({"sign": UPSTREAM_PAT_SIGN}) == UPSTREAM_PAT_CIPHERTEXT
    assert encryptor.decrypt(UPSTREAM_PAT_CIPHERTEXT) == {"sign": UPSTREAM_PAT_SIGN}


def test_encryptor_rotation_entries():
    old_entries = [("aes-128-cbc", b"0123456789abcdef", b"fedcba9876543210")]
    old_cipher = Encryptor(old_entries).encrypt({"sign": "x"})
    rotated = Encryptor([*PAT_ENTRIES, *old_entries])
    assert rotated.decrypt(old_cipher) == {"sign": "x"}
    # new encryptions use entries[0]
    assert rotated.encrypt({"sign": "x"}) == Encryptor(PAT_ENTRIES).encrypt({"sign": "x"})
    with pytest.raises(ValueError):
        rotated.decrypt(base64.b64encode(b"garbage!!").decode())


def test_get_and_split_access_token_roundtrip():
    token_id = "accFixtureTokenId1"
    token = get_access_token(token_id, UPSTREAM_PAT_SIGN)
    prefix, rest_id, ciphertext = token.split("_")
    assert prefix == pat_prefix()
    assert rest_id == token_id
    assert ciphertext == UPSTREAM_PAT_CIPHERTEXT  # deterministic CBC with fixed key/iv
    parsed = split_access_token(token)
    assert parsed == {
        "prefix": pat_prefix(),
        "accessTokenId": token_id,
        "sign": UPSTREAM_PAT_SIGN,
    }


def test_split_access_token_rejects_invalid():
    assert split_access_token("bogus") is None
    assert split_access_token("wrongprefix_accX_aaaa") is None
    assert split_access_token(f"{pat_prefix()}_accX_not-base64!!") is None
    other = Encryptor([("aes-128-cbc", b"0123456789abcdef", b"fedcba9876543210")])
    foreign = other.encrypt({"sign": "y"})
    assert split_access_token(f"{pat_prefix()}_accX_{foreign}") is None
    # ciphertext decrypting to a payload without a sign
    nosign = Encryptor(PAT_ENTRIES).encrypt({"nosign": 1})
    assert split_access_token(f"{pat_prefix()}_accX_{nosign}") is None


# ---------------------------------------------------------------------------
# JWT facade
# ---------------------------------------------------------------------------


def test_jwt_sign_verify_roundtrip():
    service = JwtService([JWT_SECRET], default_expires_in="20d")
    token = service.sign({"userId": "usrJwt"})
    claims = service.verify(token)
    assert claims["userId"] == "usrJwt"
    assert claims["exp"] - claims["iat"] == ms("20d") // 1000


def test_jwt_rotation_and_expiry():
    old = JwtService(["old-secret"], default_expires_in="1d")
    old_token = old.sign({"userId": "usrJwt"})
    rotated = JwtService(["new-secret", "old-secret"], default_expires_in="1d")
    assert rotated.verify(old_token)["userId"] == "usrJwt"
    assert rotated.classify_signing_secret(old_token) == "old"
    assert rotated.classify_signing_secret(rotated.sign({"userId": "u"})) == "current"
    assert rotated.classify_signing_secret("garbage") == "none"

    import jwt as pyjwt

    with pytest.raises(pyjwt.InvalidTokenError):
        JwtService(["wrong-secret"]).verify(old_token)

    expired = pyjwt.encode(
        {"userId": "usrJwt", "exp": int(time.time()) - 10}, JWT_SECRET, algorithm="HS256"
    )
    with pytest.raises(pyjwt.ExpiredSignatureError):
        JwtService([JWT_SECRET]).verify(expired)


def test_jwt_exp_claim_not_overwritten():
    service = JwtService([JWT_SECRET])
    exp = int(time.time()) + 500
    token = service.sign({"userId": "u", "exp": exp})
    assert service.verify(token)["exp"] == exp


# ---------------------------------------------------------------------------
# permission matrix (owner/creator/editor/commenter/viewer)
# ---------------------------------------------------------------------------


PERMISSION_MATRIX = [
    # (role, action, expected)
    (Role.OWNER, "space|delete", True),
    (Role.OWNER, "base|authority_matrix_config", True),
    (Role.OWNER, "instance|read", False),
    (Role.CREATOR, "space|delete", False),
    (Role.CREATOR, "base|create", True),
    (Role.CREATOR, "base|db_connection", False),
    (Role.CREATOR, "table|create", True),
    (Role.EDITOR, "table|create", False),
    (Role.EDITOR, "record|create", True),
    (Role.EDITOR, "field|create", False),
    (Role.EDITOR, "view|share", True),
    (Role.EDITOR, "table|trash_reset", False),
    (Role.COMMENTER, "record|comment", True),
    (Role.COMMENTER, "record|create", False),
    (Role.COMMENTER, "base|query_data", False),
    (Role.COMMENTER, "table_record_history|read", False),
    (Role.VIEWER, "record|read", True),
    (Role.VIEWER, "record|comment", False),
    (Role.VIEWER, "base|query_data", True),
    (Role.VIEWER, "view|update", False),
]


@pytest.mark.parametrize("role,action,expected", PERMISSION_MATRIX)
def test_role_permission_matrix(role, action, expected):
    assert has_permission(role, action) is expected


def test_get_permissions_matches_matrix_complement():
    for role in Role:
        perms = set(get_permissions(role))
        # every granted action is a known action; owner lacks only instance/enterprise
        denied = {"instance|read", "instance|update", "enterprise|read", "enterprise|update"}
        assert perms.isdisjoint(denied)
    assert len(get_permissions(Role.OWNER)) == 57
    assert len(get_permissions(Role.CREATOR)) == 52
    assert len(get_permissions(Role.EDITOR)) == 31
    assert len(get_permissions(Role.COMMENTER)) == 17
    assert len(get_permissions(Role.VIEWER)) == 17


def test_check_permissions_and_restricted():
    assert check_permissions(Role.EDITOR, ["record|create", "record|delete"]) is True
    assert check_permissions(Role.EDITOR, ["record|create", "table|delete"]) is False
    assert is_restricted_role(Role.VIEWER) is True
    assert is_restricted_role(Role.OWNER) is False


def test_template_permissions_exact():
    assert TEMPLATE_PERMISSIONS == [
        "base|read",
        "table|read",
        "view|read",
        "field|read",
        "record|read",
        "automation|read",
        "app|read",
        "base|query_data",
    ]


def test_get_max_level_role():
    collaborators = [
        {"role_name": "viewer"},
        {"role_name": "editor"},
        {"role_name": "commenter"},
    ]
    assert get_max_level_role(collaborators) == Role.EDITOR
    assert get_max_level_role([]) is None


def test_is_anonymous():
    assert is_anonymous(ANONYMOUS_USER_ID) is True
    assert is_anonymous("usrX") is False
    assert is_anonymous(None) is False


# ---------------------------------------------------------------------------
# cls request context
# ---------------------------------------------------------------------------


def test_cls_dotpath_get_set_and_reset():
    token = wedoc_cls.enter({"id": "req-1"})
    try:
        assert wedoc_cls.get_request_id() == "req-1"
        wedoc_cls.set("user.id", "usrA")
        wedoc_cls.set("user.isAdmin", True)
        assert wedoc_cls.get("user.id") == "usrA"
        assert wedoc_cls.get("user") == {"id": "usrA", "isAdmin": True}
        assert wedoc_cls.get("user.email") is None
        wedoc_cls.set("accessTokenId", "accX")
        assert wedoc_cls.get("accessTokenId") == "accX"
    finally:
        wedoc_cls.exit(token)
    assert wedoc_cls.get("id") is None


# ---------------------------------------------------------------------------
# auth guard strategy chain (session -> access-token -> jwt -> anonymous)
# ---------------------------------------------------------------------------

from types import SimpleNamespace  # noqa: E402

from fastapi import Request  # noqa: E402

from wedoc.core.errors import ApiError, HttpErrorCode  # noqa: E402
from wedoc.core.security.auth import (  # noqa: E402
    AuthGuard,
    EnsureLoginRedirect,
    _AccessTokenValidator,
    allow_anonymous,
    ensure_login,
    public,
)
from wedoc.core.security.session import SessionHandle  # noqa: E402

GUARD_USER = {
    "id": "usrGuard",
    "name": "Guard",
    "email": "guard@example.com",
    "avatar": None,
    "phone": None,
    "password": "hashed",
    "notify_meta": None,
    "is_admin": None,
    "lang": None,
    "deactivated_time": None,
    "is_system": None,
}


def make_request(
    endpoint,
    headers: list[tuple[bytes, bytes]] | None = None,
    path: str = "/api/space",
) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "query_string": b"",
        "headers": headers or [],
        "scheme": "http",
        "server": ("localhost", 80),
        "route": SimpleNamespace(endpoint=endpoint),
    }
    return Request(scope)


async def stub_user_loader(user_id: str):
    return GUARD_USER if user_id == GUARD_USER["id"] else None


async def test_guard_session_strategy(cache):
    store = SessionStore(cache, session_expires_in="7d")
    guard = AuthGuard(
        session_handle=SessionHandle(store, secrets=[SESSION_SECRET]),
        user_loader=stub_user_loader,
    )
    sid = generate_sid()
    await store.set(sid, new_session_data(GUARD_USER["id"]))
    cookie = encode_cookie_value(sign_sid(sid, SESSION_SECRET))

    @allow_anonymous()
    def endpoint():
        pass

    token = wedoc_cls.enter()
    try:
        request = make_request(endpoint, [(b"cookie", f"auth_session={cookie}".encode())])
        user = await guard.authorize(request)
        assert user["id"] == GUARD_USER["id"]
        assert wedoc_cls.get("user.id") == GUARD_USER["id"]
        assert wedoc_cls.get("accessTokenId") is None
    finally:
        wedoc_cls.exit(token)
        await store.destroy(sid)


async def test_guard_session_unknown_user_is_401(cache):
    store = SessionStore(cache, session_expires_in="7d")
    guard = AuthGuard(
        session_handle=SessionHandle(store, secrets=[SESSION_SECRET]),
        user_loader=stub_user_loader,
    )
    sid = generate_sid()
    await store.set(sid, new_session_data("usrGhost"))
    cookie = encode_cookie_value(sign_sid(sid, SESSION_SECRET))

    @allow_anonymous()
    def endpoint():
        pass

    token = wedoc_cls.enter()
    try:
        request = make_request(endpoint, [(b"cookie", f"auth_session={cookie}".encode())])
        with pytest.raises(ApiError) as exc_info:
            await guard.authorize(request)
        assert exc_info.value.status == 401
    finally:
        wedoc_cls.exit(token)
        await store.destroy(sid)


class _StubAccessTokenValidator(_AccessTokenValidator):
    async def validate(self, access_token_id: str, sign: str) -> tuple[str, str]:
        if sign != UPSTREAM_PAT_SIGN:
            raise ApiError("sign error", HttpErrorCode.UNAUTHORIZED)
        return GUARD_USER["id"], access_token_id


async def test_guard_access_token_strategy():
    guard = AuthGuard(
        session_handle=SessionHandle(SessionStore.__new__(SessionStore), secrets=[SESSION_SECRET]),
        user_loader=stub_user_loader,
        access_token_validator=_StubAccessTokenValidator(),
    )
    token_value = get_access_token("accGuardTest", UPSTREAM_PAT_SIGN)

    @allow_anonymous()
    def endpoint():
        pass

    token = wedoc_cls.enter()
    try:
        request = make_request(endpoint, [(b"authorization", f"Bearer {token_value}".encode())])
        user = await guard.authorize(request)
        assert user["id"] == GUARD_USER["id"]
        assert wedoc_cls.get("accessTokenId") == "accGuardTest"
    finally:
        wedoc_cls.exit(token)


async def test_guard_jwt_strategy():
    jwt_service = JwtService([JWT_SECRET], default_expires_in="20d")
    guard = AuthGuard(
        session_handle=SessionHandle(SessionStore.__new__(SessionStore), secrets=[SESSION_SECRET]),
        user_loader=stub_user_loader,
        jwt_service=jwt_service,
    )
    token_value = jwt_service.sign({"userId": GUARD_USER["id"]})

    @allow_anonymous()
    def endpoint():
        pass

    token = wedoc_cls.enter()
    try:
        request = make_request(endpoint, [(b"authorization", f"Bearer {token_value}".encode())])
        user = await guard.authorize(request)
        assert user["id"] == GUARD_USER["id"]
    finally:
        wedoc_cls.exit(token)


async def test_guard_anonymous_rejected_without_allow_anonymous():
    guard = AuthGuard(
        session_handle=SessionHandle(SessionStore.__new__(SessionStore), secrets=[SESSION_SECRET]),
        user_loader=stub_user_loader,
    )

    def endpoint():
        pass

    token = wedoc_cls.enter()
    try:
        with pytest.raises(ApiError) as exc_info:
            await guard.authorize(make_request(endpoint))
        assert exc_info.value.status == 401
    finally:
        wedoc_cls.exit(token)


async def test_guard_anonymous_allowed_with_marker():
    guard = AuthGuard(
        session_handle=SessionHandle(SessionStore.__new__(SessionStore), secrets=[SESSION_SECRET]),
        user_loader=stub_user_loader,
    )

    @allow_anonymous()
    def endpoint():
        pass

    token = wedoc_cls.enter()
    try:
        user = await guard.authorize(make_request(endpoint))
        assert user["id"] == ANONYMOUS_USER_ID
    finally:
        wedoc_cls.exit(token)


async def test_guard_public_short_circuits():
    guard = AuthGuard(
        session_handle=SessionHandle(SessionStore.__new__(SessionStore), secrets=[SESSION_SECRET]),
        user_loader=stub_user_loader,
    )

    @public()
    def endpoint():
        pass

    assert await guard.authorize(make_request(endpoint)) == {}


async def test_guard_ensure_login_redirects():
    guard = AuthGuard(
        session_handle=SessionHandle(SessionStore.__new__(SessionStore), secrets=[SESSION_SECRET]),
        user_loader=stub_user_loader,
    )

    @ensure_login()
    def endpoint():
        pass

    token = wedoc_cls.enter()
    try:
        with pytest.raises(EnsureLoginRedirect) as exc_info:
            await guard.authorize(make_request(endpoint, path="/api/space/spcX"))
        assert exc_info.value.redirect_url == "/auth/signup?redirect=%2Fapi%2Fspace%2FspcX"
    finally:
        wedoc_cls.exit(token)
