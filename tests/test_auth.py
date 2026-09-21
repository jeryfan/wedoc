"""Endpoint tests for /api/auth.

Run against the dev PG/Redis (same instances as the parity harness); skipped
when either is unreachable. Tests create their own users with unique emails,
so the dev DB does not need resetting between runs.
"""

import base64
import json
import time

from conftest import signup as _signup
from conftest import uniq_email as _uniq_email

from wedoc.compat import system_email
from wedoc.core.security.session import parse_cookie_header

# ---------------------------------------------------------------------------
# signup / signin / session
# ---------------------------------------------------------------------------


async def test_signup_returns_user_me_shape_and_sets_session(client):
    email, resp = await _signup(client)
    body = resp.json()
    assert body["email"] == email
    assert body["name"] == email.split("@")[0]
    assert body["hasPassword"] is True
    assert body["notifyMeta"] == {"email": True}
    assert body["avatar"].startswith("http://localhost:3000/api/attachments/read/public/avatar/usr")
    assert body["phone"] is None
    assert body["lang"] == "en"
    cookies = parse_cookie_header(resp.headers["set-cookie"])
    assert "auth_session" in cookies

    me = await client.get("/api/auth/user/me")
    assert me.status_code == 200
    assert me.json()["id"] == body["id"]


async def test_signup_lowercases_email(client):
    local = f"Test-{int(time.time() * 1000)}"
    resp = await client.post(
        "/api/auth/signup",
        json={"email": f"{local}@EXAMPLE.com", "password": "Passw0rd!1"},
    )
    assert resp.status_code == 201
    assert resp.json()["email"] == f"{local.lower()}@example.com"
    assert resp.json()["name"] == local.lower()


async def test_signup_duplicate_email_conflict(client):
    email, _ = await _signup(client)
    resp = await client.post("/api/auth/signup", json={"email": email, "password": "Passw0rd!1"})
    assert resp.status_code == 409
    assert resp.json() == {
        "message": f"User {email} is already registered",
        "status": 409,
        "code": "conflict",
        "data": {"localization": {"i18nKey": "httpErrors.auth.alreadyRegistered"}},
    }


async def test_signup_validation_messages(client):
    resp = await client.post("/api/auth/signup", json={})
    assert resp.status_code == 400
    assert resp.json()["message"] == (
        'Validation error: Invalid input: expected string, received undefined at "email"; '
        'Invalid input: expected string, received undefined at "password"'
    )

    resp = await client.post(
        "/api/auth/signup", json={"email": "bad", "password": "weak"}
    )
    assert resp.json()["message"] == (
        'Validation error: Invalid email address at "email"; '
        'Too small: expected string to have >=8 characters at "password"; '
        'Must contain at least one letter and one number at "password"'
    )

    resp = await client.post(
        "/api/auth/signup", json={"email": "a@b.co", "password": "NoDigitsHere!"}
    )
    assert resp.json()["message"] == (
        'Validation error: Must contain at least one letter and one number at "password"'
    )


async def test_signup_lang_from_accept_language(client):
    email = _uniq_email()
    resp = await client.post(
        "/api/auth/signup",
        json={"email": email, "password": "Passw0rd!1"},
        headers={"Accept-Language": "zh-CN"},
    )
    assert resp.json()["lang"] == "zh"


async def test_signin_flow_and_errors(client):
    email, _ = await _signup(client)
    client.cookies.clear()

    resp = await client.post("/api/auth/signin", json={"email": email, "password": "Passw0rd!1"})
    assert resp.status_code == 200
    assert resp.json()["email"] == email

    resp = await client.post("/api/auth/signin", json={"email": email, "password": "WrongPass0!"})
    assert resp.status_code == 400
    assert resp.json() == {
        "message": "Email or password is incorrect",
        "status": 400,
        "code": "invalid_credentials",
        "data": {"localization": {"i18nKey": "httpErrors.auth.emailOrPasswordIncorrect"}},
    }

    resp = await client.post(
        "/api/auth/signin", json={"email": "nobody@example.com", "password": "Passw0rd!1"}
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "invalid_credentials"

    # passport-local: missing credentials -> 401, no zod pipe on signin
    resp = await client.post("/api/auth/signin", json={"email": email})
    assert resp.status_code == 401
    assert resp.json() == {"message": "Unauthorized", "status": 401, "code": "unauthorized"}

    resp = await client.post("/api/auth/signin", json={"email": "not-an-email", "password": "x"})
    assert resp.status_code == 400
    assert resp.json()["code"] == "invalid_credentials"


async def test_signout_clears_session(client):
    await _signup(client)
    resp = await client.post("/api/auth/signout")
    assert resp.status_code == 200
    assert resp.content == b""
    me = await client.get("/api/auth/user/me")
    assert me.json() == {
        "id": "anonymous",
        "name": "Anonymous",
        "email": system_email("anonymous"),
    }


async def test_me_anonymous_without_session(client):
    resp = await client.get("/api/auth/user/me")
    assert resp.status_code == 200
    assert resp.json()["id"] == "anonymous"


async def test_auth_user_endpoint(client):
    email, signup = await _signup(client)
    resp = await client.get("/api/auth/user")
    assert resp.status_code == 200
    assert resp.json() == {
        "id": signup.json()["id"],
        "email": email,
        "avatar": signup.json()["avatar"],
        "name": email.split("@")[0],
    }


async def test_temp_token_then_bearer(client):
    _, signup = await _signup(client)
    resp = await client.get("/api/auth/temp-token")
    assert resp.status_code == 200
    token = resp.json()["accessToken"]
    expires = resp.json()["expiresTime"]
    assert expires.endswith("Z") and "." in expires

    client.cookies.clear()
    me = await client.get(
        "/api/auth/user/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert me.status_code == 200
    assert me.json()["id"] == signup.json()["id"]


async def test_social_auth_routes_unregistered(client):
    for path in (
        "/api/auth/github",
        "/api/auth/github/callback",
        "/api/auth/google",
        "/api/auth/oidc",
    ):
        resp = await client.get(path)
        assert resp.status_code == 404
        assert resp.json() == {
            "message": f"Cannot GET {path}",
            "status": 404,
            "code": "not_found",
        }


# ---------------------------------------------------------------------------
# password lifecycle
# ---------------------------------------------------------------------------


async def test_change_password_flow_revokes_sessions(client):
    email, _ = await _signup(client)
    resp = await client.patch(
        "/api/auth/change-password",
        json={"password": "WrongPass0!", "newPassword": "NewPassw0rd!"},
    )
    assert resp.status_code == 400
    assert resp.json()["data"]["localization"]["i18nKey"] == "httpErrors.auth.passwordIncorrect"

    resp = await client.patch(
        "/api/auth/change-password",
        json={"password": "Passw0rd!1", "newPassword": "weak"},
    )
    assert resp.status_code == 400
    assert "Too small" in resp.json()["message"]

    resp = await client.patch(
        "/api/auth/change-password",
        json={"password": "Passw0rd!1", "newPassword": "NewPassw0rd!"},
    )
    assert resp.status_code == 200
    assert resp.content == b""

    # old session revoked
    assert (await client.get("/api/auth/temp-token")).status_code == 401

    resp = await client.post("/api/auth/signin", json={"email": email, "password": "NewPassw0rd!"})
    assert resp.status_code == 200


async def test_add_password_conflict_when_set(client):
    await _signup(client)
    resp = await client.post("/api/auth/add-password", json={"password": "An0therPass!"})
    assert resp.status_code == 400
    assert resp.json()["message"] == "Password is already set"


async def test_reset_password_flow(client, db):
    import redis.asyncio as aioredis

    from wedoc.config import get_settings

    email, signup_resp = await _signup(client)
    user_id = signup_resp.json()["id"]
    resp = await client.post("/api/auth/send-reset-password-email", json={"email": email})
    assert resp.status_code == 201
    assert resp.content == b""

    resp = await client.post(
        "/api/auth/send-reset-password-email", json={"email": "nobody@example.com"}
    )
    assert resp.status_code == 400
    assert resp.json()["message"] == "nobody@example.com not registered"

    r = aioredis.from_url(get_settings().backend_cache_redis_uri)
    from wedoc.compat import cache_key_namespace

    code = None
    async for key in r.scan_iter(f"{cache_key_namespace()}:reset-password-email:*"):
        entry = json.loads(await r.get(key))
        if (entry.get("value") or {}).get("userId") == user_id:
            code = key.decode().split(":")[-1]
    await r.aclose()
    assert code

    resp = await client.post(
        "/api/auth/reset-password", json={"code": "bogus", "password": "NewPassw0rd!"}
    )
    assert resp.status_code == 400
    assert resp.json()["message"] == "Token is invalid"

    resp = await client.post(
        "/api/auth/reset-password", json={"code": code, "password": "NewPassw0rd!"}
    )
    assert resp.status_code == 201

    # one-time code
    resp = await client.post(
        "/api/auth/reset-password", json={"code": code, "password": "NewPassw0rd!"}
    )
    assert resp.status_code == 400

    resp = await client.post("/api/auth/signin", json={"email": email, "password": "NewPassw0rd!"})
    assert resp.status_code == 200


async def test_send_signup_verification_code(client):
    email = _uniq_email()
    resp = await client.post("/api/auth/send-signup-verification-code", json={"email": email})
    assert resp.status_code == 200
    body = resp.json()
    assert "token" in body and body["expiresTime"].endswith("Z")

    # repeat within the rate window -> 429
    resp = await client.post("/api/auth/send-signup-verification-code", json={"email": email})
    assert resp.status_code == 429
    assert resp.json() == {
        "message": "Reached the rate limit of sending mail, please try again after 28 seconds",
        "status": 429,
        "code": "too_many_requests",
        "data": {"seconds": 30},
    }


async def test_send_signup_verification_code_registered_conflict(client):
    email, _ = await _signup(client)
    resp = await client.post("/api/auth/send-signup-verification-code", json={"email": email})
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# email change
# ---------------------------------------------------------------------------


async def test_change_email_flow(client):
    email, signup = await _signup(client)
    new_email = _uniq_email()

    resp = await client.post(
        "/api/auth/send-change-email-code", json={"email": new_email, "password": "WrongPass0!"}
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "invalid_credentials"

    resp = await client.post(
        "/api/auth/send-change-email-code", json={"email": email, "password": "Passw0rd!1"}
    )
    assert resp.status_code == 409
    assert resp.json()["data"]["localization"]["i18nKey"] == (
        "httpErrors.auth.newEmailSameAsCurrentEmail"
    )

    resp = await client.post(
        "/api/auth/send-change-email-code", json={"email": new_email, "password": "Passw0rd!1"}
    )
    assert resp.status_code == 200
    token = resp.json()["token"]

    payload = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))
    code = payload["code"]
    assert payload["email"] == email and payload["newEmail"] == new_email

    resp = await client.patch(
        "/api/auth/change-email", json={"email": new_email, "token": token, "code": "0000"}
    )
    assert resp.status_code == 400
    assert resp.json()["data"]["localization"]["i18nKey"] == (
        "httpErrors.auth.verificationCodeInvalid"
    )

    resp = await client.patch(
        "/api/auth/change-email", json={"email": new_email, "token": "bogus", "code": code}
    )
    assert resp.status_code == 400
    assert "data" not in resp.json()

    resp = await client.patch(
        "/api/auth/change-email", json={"email": new_email, "token": token, "code": code}
    )
    assert resp.status_code == 200

    # session revoked; signin with the new email works
    resp = await client.post(
        "/api/auth/signin", json={"email": new_email, "password": "Passw0rd!1"}
    )
    assert resp.status_code == 200
    assert resp.json()["email"] == new_email
    assert resp.json()["id"] == signup.json()["id"]


# ---------------------------------------------------------------------------
# waitlist
# ---------------------------------------------------------------------------


async def test_join_waitlist_disabled(client):
    resp = await client.post("/api/auth/join-waitlist", json={"email": "w@example.com"})
    assert resp.status_code == 400
    assert resp.json()["data"]["localization"]["i18nKey"] == "httpErrors.auth.waitlistNotEnabled"


async def test_waitlist_admin_gating(client, db):
    email, _ = await _signup(client)
    resp = await client.get("/api/auth/waitlist")
    assert resp.status_code == 403
    assert resp.json() == {
        "message": "User is not an admin",
        "status": 403,
        "code": "restricted_resource",
        "data": {"localization": {"i18nKey": "httpErrors.permission.userNotAdmin"}},
    }

    await db.execute("UPDATE users SET is_admin=true WHERE email=$1", email)

    resp = await client.get("/api/auth/waitlist")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

    resp = await client.post("/api/auth/waitlist-invite-code", json={"count": 2, "times": 5})
    assert resp.status_code == 201
    codes = resp.json()
    assert len(codes) == 2
    assert all(len(c["code"]) == 9 and c["code"][4] == "-" and c["times"] == 5 for c in codes)

    resp = await client.post("/api/auth/waitlist-invite-code", json={"count": 1.5, "times": 2})
    assert resp.json()["message"] == (
        'Validation error: Invalid input: expected int, received number at "count"'
    )

    resp = await client.post("/api/auth/invite-waitlist", json={"list": ["nobody@example.com"]})
    assert resp.status_code == 201
    assert resp.json() == []

    resp = await client.post("/api/auth/invite-waitlist", json={"list": ["bad"]})
    assert resp.json()["message"] == 'Validation error: Invalid email address at "list[0]"'


async def test_waitlist_invite_flow_with_setting(client, db):
    """enableWaitlist=true: join, invite, then signup consumes the invite code."""
    email, _ = await _signup(client)
    await db.execute("UPDATE users SET is_admin=true WHERE email=$1", email)
    await db.execute(
        "INSERT INTO setting (name, content, created_by) VALUES ('enableWaitlist', 'true', 'test') "
        "ON CONFLICT (name) DO UPDATE SET content='true'"
    )
    try:
        wait_email = _uniq_email()
        resp = await client.post("/api/auth/join-waitlist", json={"email": wait_email})
        assert resp.status_code == 201
        assert resp.json() == {"email": wait_email}

        rows = await client.get("/api/auth/waitlist")
        assert any(r["email"] == wait_email and r["invite"] is None for r in rows.json())

        resp = await client.post("/api/auth/invite-waitlist", json={"list": [wait_email]})
        assert resp.status_code == 201
        invited = resp.json()
        assert invited[0]["email"] == wait_email and invited[0]["times"] == 10
        code = invited[0]["code"]

        rows = await client.get("/api/auth/waitlist")
        invited_row = next(r for r in rows.json() if r["email"] == wait_email)
        assert invited_row["invite"] is True and invited_row["inviteTime"]

        # signup without code rejected, with code accepted
        fresh = _uniq_email()
        resp = await client.post(
            "/api/auth/signup", json={"email": fresh, "password": "Passw0rd!1"}
        )
        assert resp.status_code == 400
        assert resp.json()["data"]["localization"]["i18nKey"] == (
            "httpErrors.user.waitlistInviteCodeRequired"
        )
        client.cookies.clear()
        resp = await client.post(
            "/api/auth/signup",
            json={"email": fresh, "password": "Passw0rd!1", "inviteCode": code},
        )
        assert resp.status_code == 201
    finally:
        await db.execute("DELETE FROM setting WHERE name='enableWaitlist'")


# ---------------------------------------------------------------------------
# delete user
# ---------------------------------------------------------------------------


async def test_delete_user(client):
    email, _ = await _signup(client)
    resp = await client.delete("/api/auth/user", params={"confirm": "no"})
    assert resp.status_code == 400
    assert resp.json()["message"] == (
        'Validation error: Please enter DELETE to confirm at "confirm"'
    )

    resp = await client.delete("/api/auth/user")
    assert resp.status_code == 400
    assert "received undefined" in resp.json()["message"]

    resp = await client.delete("/api/auth/user", params={"confirm": "DELETE"})
    assert resp.status_code == 200
    assert resp.content == b""

    me = await client.get("/api/auth/user/me")
    assert me.json()["id"] == "anonymous"

    resp = await client.post("/api/auth/signin", json={"email": email, "password": "Passw0rd!1"})
    assert resp.status_code == 400
    assert resp.json()["code"] == "invalid_credentials"

    # the freed email can sign up again
    resp = await client.post("/api/auth/signup", json={"email": email, "password": "Passw0rd!1"})
    assert resp.status_code == 201


async def test_delete_user_with_space_rejected(client, db):
    _email, signup = await _signup(client)
    user_id = signup.json()["id"]
    await db.execute(
        "INSERT INTO space (id, name, created_by) VALUES ('spcT1', 'My Space', $1)", user_id
    )
    await db.execute(
        "INSERT INTO collaborator (id, resource_type, resource_id, principal_type,"
        " principal_id, role_name, created_by) "
        "VALUES ('collabT1', 'space', 'spcT1', 'user', $1, 'owner', $1)",
        user_id,
    )
    try:
        resp = await client.delete("/api/auth/user", params={"confirm": "DELETE"})
        assert resp.status_code == 400
        body = resp.json()
        assert body["message"] == (
            "User has collaborators in spaces (or deleted spaces in trash): My Space"
        )
        assert body["data"]["spaces"] == [
            {"id": "spcT1", "name": "My Space", "deletedTime": None}
        ]
    finally:
        await db.execute("DELETE FROM collaborator WHERE id='collabT1'")
        await db.execute("DELETE FROM space WHERE id='spcT1'")


async def test_bcrypt_long_password_roundtrip(client):
    """Passwords >72 bytes are truncated like node bcrypt."""
    email = _uniq_email()
    long_pw = "Aa1!" + "x" * 100
    resp = await client.post("/api/auth/signup", json={"email": email, "password": long_pw})
    assert resp.status_code == 201
    client.cookies.clear()
    # a different suffix beyond byte 72 still matches (truncation semantics)
    resp = await client.post(
        "/api/auth/signin", json={"email": email, "password": long_pw[:72] + "DIFFERENT-SUFFIX"}
    )
    assert resp.status_code == 200
    client.cookies.clear()
    # a difference within the first 72 bytes does not
    resp = await client.post(
        "/api/auth/signin",
        json={"email": email, "password": "WRONG!" + long_pw[6:]},
    )
    assert resp.status_code == 400
