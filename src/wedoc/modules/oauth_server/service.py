"""OAuth server + device-grant service — ports oauth-server.service.ts + oauth-device.service.ts.

The authorization-code exchange and device-grant token minting reach the same
machine-token issuance deferred elsewhere; the deterministic surface (device
code issuance, consent/decision lookups, login gating, client auth) is ported.
"""

import base64
import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
from sqlalchemy import select

from ...config import get_settings
from ...core.cache import get_cache, ms, second
from ...core.errors import ApiError, HttpErrorCode
from ...core.ids import IdPrefix, cuid, new_id, random_string
from ...core.security.auth import JwtService, get_access_token
from ...db import engine as db_engine
from ...db.models_meta import OAuthApp, OAuthAppAuthorized, OAuthAppSecret, OAuthAppToken

DEVICE_VERIFICATION_PATH = "/oauth/device"
DEVICE_CODE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
_USER_CODE_ALPHABET = "BCDFGHJKMNPQRSTVWXZ"


class OAuthTokenError(Exception):
    """Maps to an RFC 6749 error envelope (device grant) or an HTTP error."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


class DeviceAuthorizationError(Exception):
    def __init__(self, rfc_error: str, description: str) -> None:
        super().__init__(description)
        self.rfc_error = rfc_error
        self.description = description


def _origin() -> str:
    return get_settings().public_origin.rstrip("/")


def _generate_user_code() -> str:
    groups = [
        "".join(secrets.choice(_USER_CODE_ALPHABET) for _ in range(4)) for _ in range(2)
    ]
    return "-".join(groups)


async def _get_app(client_id: str) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(
                    OAuthApp.name,
                    OAuthApp.description,
                    OAuthApp.homepage,
                    OAuthApp.logo,
                    OAuthApp.scopes,
                    OAuthApp.allow_device_flow,
                    OAuthApp.redirect_uris,
                ).where(OAuthApp.client_id == client_id)
            )
        ).first()
    if row is None:
        return None
    return {
        "name": row[0],
        "description": row[1],
        "homepage": row[2],
        "logo": row[3],
        "scopes": json.loads(row[4]) if row[4] else [],
        "allowDeviceFlow": bool(row[5]),
        "redirectUris": json.loads(row[6]) if row[6] else [],
    }


def _split_scopes(scope: str | None) -> list[str]:
    if not scope:
        return []
    return [s for s in scope.replace(",", " ").split() if s]


def _append_query(url: str, params: dict[str, Any]) -> str:
    from urllib.parse import urlencode

    pairs = {k: v for k, v in params.items() if v is not None}
    if not pairs:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{urlencode(pairs)}"


async def _list_secret_hashes(client_id: str) -> list[tuple[str, str]]:
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(OAuthAppSecret.id, OAuthAppSecret.secret).where(
                    OAuthAppSecret.client_id == client_id
                )
            )
        ).all()
    return [(r[0], r[1]) for r in rows]


async def _touch_secret_last_used(secret_id: str) -> None:
    # the client-password strategy stamps lastUsedTime on each successful auth.
    from sqlalchemy import update

    async with db_engine.session() as session:
        await session.execute(
            update(OAuthAppSecret)
            .where(OAuthAppSecret.id == secret_id)
            .values(last_used_time=datetime.now(UTC).replace(tzinfo=None))
        )
        await session.commit()


async def _rotate_app_token(
    client_id: str, old_sign: str, secret_id: str | None, new_sign: str, expired: datetime
) -> bool:
    from sqlalchemy import update

    async with db_engine.session() as session:
        conditions = [
            OAuthAppToken.client_id == client_id,
            OAuthAppToken.refresh_token_sign == old_sign,
        ]
        if secret_id is not None:
            conditions.append(OAuthAppToken.app_secret_id == secret_id)
        result = await session.execute(
            update(OAuthAppToken)
            .where(*conditions)
            .values(refresh_token_sign=new_sign, expired_time=expired)
        )
        await session.commit()
    return result.rowcount > 0


class OAuthDeviceService:
    async def request_device_code(
        self, client_id: str | None, scope: str | None
    ) -> dict[str, Any]:
        if not client_id:
            raise DeviceAuthorizationError("invalid_request", "client_id is required")
        app = await _get_app(client_id)
        if app is None:
            raise DeviceAuthorizationError("invalid_client", "Unknown client")
        if not app["allowDeviceFlow"]:
            raise DeviceAuthorizationError(
                "unauthorized_client",
                "This app has not enabled the device authorization flow",
            )
        scopes = [s for s in (scope or "").replace(",", " ").split() if s]
        invalid = [s for s in scopes if s not in app["scopes"]]
        if invalid:
            raise DeviceAuthorizationError("invalid_scope", "Invalid scopes: " + ",".join(invalid))
        settings = get_settings()
        expires_in = second(settings.backend_oauth_device_code_expire_in)
        device_code = random_string(32)
        cache = get_cache()
        user_code = _generate_user_code()
        while not await cache.setnx(f"oauth:device-user:{user_code}", device_code, expires_in):
            user_code = _generate_user_code()
        state = {
            "clientId": client_id,
            "scopes": scopes if scopes else app["scopes"],
            "userCode": user_code,
            "status": "pending",
            "expiresAt": int(datetime.now(UTC).timestamp() * 1000) + expires_in * 1000,
        }
        await cache.set_detail(f"oauth:device:{device_code}", state, expires_in)
        return {
            "device_code": device_code,
            "user_code": user_code,
            "verification_uri": f"{_origin()}{DEVICE_VERIFICATION_PATH}",
            "expires_in": expires_in,
            "interval": settings.backend_oauth_device_code_interval,
        }

    async def _state_by_user_code(self, user_code: str) -> dict[str, Any] | None:
        cache = get_cache()
        normalized = user_code.upper().replace(" ", "")
        if len(normalized) == 8:
            normalized = f"{normalized[:4]}-{normalized[4:]}"
        device_code = await cache.get(f"oauth:device-user:{normalized}")
        if not device_code:
            return None
        state = await cache.get(f"oauth:device:{device_code}")
        if not state:
            return None
        return {"deviceCode": device_code, "state": state}

    async def get_device_app(self, user_code: str) -> dict[str, Any]:
        entry = await self._state_by_user_code(user_code)
        if not entry:
            raise ApiError(
                "This code has expired or does not exist", HttpErrorCode.NOT_FOUND
            )
        if entry["state"]["status"] != "pending":
            raise ApiError("This code has already been used", HttpErrorCode.VALIDATION_ERROR)
        app = await _get_app(entry["state"]["clientId"])
        if app is None or not app["allowDeviceFlow"]:
            raise ApiError("This code has already been used", HttpErrorCode.VALIDATION_ERROR)
        out: dict[str, Any] = {
            "name": app["name"],
            "homepage": app["homepage"],
            "scopes": entry["state"]["scopes"],
        }
        if app["description"] is not None:
            out["description"] = app["description"]
        if app["logo"] is not None:
            out["logo"] = app["logo"]
        return out

    async def decide(self, user_code: str, approve: bool, user: dict[str, Any]) -> str:
        entry = await self._state_by_user_code(user_code)
        if not entry or entry["state"]["status"] != "pending":
            raise ApiError("This code has already been used", HttpErrorCode.VALIDATION_ERROR)
        state = entry["state"]
        state["status"] = "approved" if approve else "denied"
        if approve:
            state["user"] = user
        cache = get_cache()
        remaining = max(
            1, (state["expiresAt"] - int(datetime.now(UTC).timestamp() * 1000)) // 1000
        )
        await cache.set_detail(f"oauth:device:{entry['deviceCode']}", state, remaining)
        return state["clientId"]

    async def poll(self, device_code: str, client_id: str) -> dict[str, Any]:
        """RFC 8628 poll leg: returns {status, state?}. Consumes the code on claim."""
        cache = get_cache()
        state = await cache.get(f"oauth:device:{device_code}")
        if not state or state.get("clientId") != client_id:
            return {"status": "expired"}
        now = int(datetime.now(UTC).timestamp() * 1000)
        if state["expiresAt"] <= now:
            await self._forget(device_code, state["userCode"])
            return {"status": "expired"}
        interval = get_settings().backend_oauth_device_code_interval
        if not await cache.setnx(f"oauth:device-poll:{device_code}", now, interval):
            return {"status": "slow_down"}
        if state["status"] == "denied":
            await self._forget(device_code, state["userCode"])
            return {"status": "denied"}
        if state["status"] == "approved" and state.get("user"):
            app = await _get_app(state["clientId"])
            if app is None or not app["allowDeviceFlow"]:
                await self._forget(device_code, state["userCode"])
                return {"status": "denied"}
            # the delete is the claim: only the poll that removes it issues tokens
            if not await cache.delete(f"oauth:device:{device_code}"):
                return {"status": "expired"}
            await cache.delete(f"oauth:device-user:{state['userCode']}")
            return {"status": "approved", "state": state}
        return {"status": "pending"}

    async def restore(self, device_code: str, state: dict[str, Any]) -> None:
        cache = get_cache()
        remaining = max(
            1, (state["expiresAt"] - int(datetime.now(UTC).timestamp() * 1000)) // 1000
        )
        await cache.set_detail(f"oauth:device:{device_code}", state, remaining)
        await cache.set(f"oauth:device-user:{state['userCode']}", device_code, remaining)

    async def _forget(self, device_code: str, user_code: str) -> None:
        cache = get_cache()
        await cache.delete(f"oauth:device:{device_code}")
        await cache.delete(f"oauth:device-user:{user_code}")


class OAuthServerService:
    async def touch_authorize(self, client_id: str, user_id: str) -> None:

        now = datetime.now(UTC).replace(tzinfo=None)
        async with db_engine.session() as session:
            existing = (
                await session.execute(
                    select(OAuthAppAuthorized.id).where(
                        OAuthAppAuthorized.client_id == client_id,
                        OAuthAppAuthorized.user_id == user_id,
                    )
                )
            ).first()
            if existing is None:
                await session.execute(
                    OAuthAppAuthorized.__table__.insert().values(
                        id=cuid(),
                        client_id=client_id,
                        user_id=user_id,
                        authorized_time=now,
                    )
                )
                await session.commit()

    async def decide_device(self, user_code: str, approve: bool, user: dict[str, Any]) -> None:
        client_id = await OAuthDeviceService().decide(user_code, approve, user)
        if approve:
            await self.touch_authorize(client_id, user["id"])

    async def validate_authorize(self, params: dict[str, Any]) -> dict[str, Any]:
        client_id = params.get("client_id")
        app = await _get_app(client_id) if client_id else None
        if app is None:
            raise ApiError("Unknown client", HttpErrorCode.VALIDATION_ERROR)
        return app

    # -- authorization-code consent flow ---------------------------------------
    async def build_authorize_transaction(
        self, params: dict[str, Any], user: dict[str, Any]
    ) -> dict[str, Any]:
        """Validate the authorize request; return either an immediate redirect
        (trusted client) or a transaction + consent redirect."""
        client_id = params.get("client_id")
        app = await _get_app(client_id) if client_id else None
        if app is None:
            raise OAuthTokenError("Unknown client", 400)
        query_scopes = _split_scopes(params.get("scope"))
        invalid = [s for s in query_scopes if s not in app["scopes"]]
        if invalid:
            raise OAuthTokenError("Invalid scopes: " + ",".join(invalid), 400)
        redirect_uris = app["redirectUris"]
        if not redirect_uris:
            raise OAuthTokenError("Redirect uri not configured", 400)
        redirect_uri = params.get("redirect_uri") or redirect_uris[0]
        scopes = query_scopes or app["scopes"]
        code_challenge = params.get("code_challenge")
        code_challenge_method = params.get("code_challenge_method")
        if code_challenge:
            if code_challenge_method != "S256":
                raise OAuthTokenError("Invalid code challenge method", 400)
            if redirect_uri not in redirect_uris:
                raise OAuthTokenError("Invalid redirectUri", 401)
        elif redirect_uri not in redirect_uris:
            raise OAuthTokenError("Invalid redirectUri", 401)

        if await self._is_trusted(user["id"], client_id):
            await self.touch_authorize(client_id, user["id"])
            code = await self._create_code(
                client_id, redirect_uri, scopes, user, code_challenge, code_challenge_method
            )
            return {"immediate": True, "redirect": _append_query(
                redirect_uri, {"code": code, "state": params.get("state")}
            )}
        transaction_id = random_string(16)
        await get_cache().set_detail(
            f"oauth:txn:{transaction_id}",
            {
                "clientId": client_id,
                "redirectUri": redirect_uri,
                "scopes": scopes,
                "state": params.get("state"),
                "codeChallenge": code_challenge,
                "codeChallengeMethod": code_challenge_method,
                "userId": user["id"],
            },
            second(get_settings().backend_oauth_transaction_expire_in),
        )
        return {"immediate": False, "transactionId": transaction_id}

    async def get_decision_info(self, transaction_id: str) -> dict[str, Any]:
        if not transaction_id:
            raise ApiError("transaction_id is required", HttpErrorCode.VALIDATION_ERROR)
        cache = get_cache()
        tx = await cache.get(f"oauth:txn:{transaction_id}")
        if not tx:
            raise ApiError("Invalid transaction ID", HttpErrorCode.VALIDATION_ERROR)
        app = await _get_app(tx["clientId"])
        if app is None:
            raise ApiError("Client not found", HttpErrorCode.NOT_FOUND)
        out: dict[str, Any] = {
            "name": app["name"],
            "homepage": app["homepage"],
            "scopes": tx["scopes"],
        }
        if app["description"] is not None:
            out["description"] = app["description"]
        if app["logo"] is not None:
            out["logo"] = app["logo"]
        return out

    async def decision(
        self, transaction_id: str, allow: bool, user: dict[str, Any]
    ) -> str:
        if not transaction_id:
            raise ApiError("transaction_id is required", HttpErrorCode.VALIDATION_ERROR)
        cache = get_cache()
        tx = await cache.get(f"oauth:txn:{transaction_id}")
        if not tx:
            raise ApiError("Invalid transaction ID", HttpErrorCode.VALIDATION_ERROR)
        if tx.get("userId") != user["id"]:
            raise ApiError("Invalid user", HttpErrorCode.VALIDATION_ERROR)
        await cache.delete(f"oauth:txn:{transaction_id}")
        redirect_uri = tx["redirectUri"]
        if not allow:
            return _append_query(redirect_uri, {"error": "access_denied", "state": tx.get("state")})
        await self.touch_authorize(tx["clientId"], user["id"])
        code = await self._create_code(
            tx["clientId"],
            redirect_uri,
            tx["scopes"],
            user,
            tx.get("codeChallenge"),
            tx.get("codeChallengeMethod"),
        )
        return _append_query(redirect_uri, {"code": code, "state": tx.get("state")})

    async def _is_trusted(self, user_id: str, client_id: str) -> bool:
        async with db_engine.session() as session:
            row = (
                await session.execute(
                    select(OAuthAppAuthorized.authorized_time).where(
                        OAuthAppAuthorized.client_id == client_id,
                        OAuthAppAuthorized.user_id == user_id,
                    )
                )
            ).first()
        if not row or row[0] is None:
            return False
        authorized = row[0]
        if authorized.tzinfo is None:
            authorized = authorized.replace(tzinfo=UTC)
        expire_ms = ms(get_settings().backend_oauth_authorized_expire_in)
        return authorized.timestamp() * 1000 + expire_ms > datetime.now(UTC).timestamp() * 1000

    async def _create_code(
        self,
        client_id: str,
        redirect_uri: str,
        scopes: list[str],
        user: dict[str, Any],
        code_challenge: str | None,
        code_challenge_method: str | None,
    ) -> str:
        code = random_string(16)
        await get_cache().set_detail(
            f"oauth:code:{code}",
            {
                "clientId": client_id,
                "redirectUri": redirect_uri,
                "scopes": scopes,
                "user": {"id": user["id"], "email": user["email"], "name": user["name"]},
                "codeChallenge": code_challenge,
                "codeChallengeMethod": code_challenge_method,
            },
            second(get_settings().backend_oauth_code_expire_in),
        )
        return code

    # -- token endpoint --------------------------------------------------------
    async def authenticate_client(
        self, client_id: str | None, client_secret: str | None, code_verifier: str | None
    ) -> dict[str, Any]:
        if not client_id:
            raise OAuthTokenError("Unauthorized", 401)
        app = await _get_app(client_id)
        if client_secret:
            if app is None:
                raise OAuthTokenError("Client not found", 401)
            secrets_rows = await _list_secret_hashes(client_id)
            if not secrets_rows:
                raise OAuthTokenError("No secrets found for the given clientId", 401)
            for sid, secret_hash in secrets_rows:
                if bcrypt.checkpw(client_secret.encode()[:72], secret_hash.encode()):
                    await _touch_secret_last_used(sid)
                    return {
                        "type": "secret",
                        "name": app["name"],
                        "secretId": sid,
                        "clientId": client_id,
                        "clientSecret": secret_hash,
                    }
            raise OAuthTokenError("Client secret invalid", 401)
        if app is None:
            raise OAuthTokenError("Client not found", 401)
        return {
            "type": "pkce",
            "name": app["name"],
            "clientId": client_id,
            "codeVerifier": code_verifier,
        }

    async def _issue_token_pair(
        self, client: dict[str, Any], user_id: str, scopes: list[str]
    ) -> dict[str, str]:
        from ..access_token import repository as at_repo

        settings = get_settings()
        token_id = new_id(IdPrefix.ACCESS_TOKEN)
        sign = random_string(16)
        expired = datetime.now(UTC) + timedelta(
            milliseconds=ms(settings.backend_oauth_access_token_expire_in)
        )
        await at_repo.create(
            token_id=token_id,
            name=f"oauth:{client['name']}",
            description=None,
            scopes=json.dumps(scopes),
            space_ids=None,
            base_ids=None,
            user_id=user_id,
            sign=sign,
            client_id=client["clientId"],
            expired_time=expired.replace(tzinfo=None),
            has_full_access=None,
        )
        refresh_sign = random_string(16)
        if client["type"] == "pkce":
            payload = {
                "clientId": client["clientId"],
                "accessTokenId": token_id,
                "sign": refresh_sign,
            }
        else:
            payload = {
                "clientId": client["clientId"],
                "secret": client["clientSecret"],
                "accessTokenId": token_id,
                "sign": refresh_sign,
            }
        refresh_token = JwtService().sign(
            payload, expires_in=settings.backend_oauth_refresh_token_expire_in
        )
        refresh_expired = datetime.now(UTC) + timedelta(
            milliseconds=ms(settings.backend_oauth_refresh_token_expire_in)
        )
        async with db_engine.session() as session:
            await session.execute(
                OAuthAppToken.__table__.insert().values(
                    id=cuid(),
                    client_id=client["clientId"],
                    refresh_token_sign=refresh_sign,
                    app_secret_id=client.get("secretId"),
                    created_by=user_id,
                    expired_time=refresh_expired.replace(tzinfo=None),
                )
            )
            await session.commit()
        return {"accessToken": get_access_token(token_id, sign), "refreshToken": refresh_token}

    def _token_response(self, tokens: dict[str, str], scopes: list[str]) -> dict[str, Any]:
        settings = get_settings()
        return {
            "access_token": tokens["accessToken"],
            "refresh_token": tokens["refreshToken"],
            "token_type": "Bearer",
            "scopes": scopes,
            "expires_in": second(settings.backend_oauth_access_token_expire_in),
            "refresh_expires_in": second(settings.backend_oauth_refresh_token_expire_in),
        }

    async def exchange_authorization_code(
        self, client: dict[str, Any], code: str | None, redirect_uri: str | None
    ) -> dict[str, Any]:
        cache = get_cache()
        state = await cache.get(f"oauth:code:{code}") if code else None
        if not state:
            raise OAuthTokenError("Invalid code", 401)
        await cache.delete(f"oauth:code:{code}")
        if state["clientId"] != client["clientId"]:
            raise OAuthTokenError("Invalid client", 401)
        if not redirect_uri:
            raise OAuthTokenError("redirect_uri is required", 401)
        if redirect_uri != state["redirectUri"]:
            raise OAuthTokenError("Invalid redirectUri", 401)
        self._verify_exchange_client(client, state)
        tokens = await self._issue_token_pair(client, state["user"]["id"], state["scopes"])
        return self._token_response(tokens, state["scopes"])

    def _verify_exchange_client(self, client: dict[str, Any], state: dict[str, Any]) -> None:
        if client["type"] == "pkce":
            verifier = client.get("codeVerifier")
            if not verifier:
                raise OAuthTokenError("code_verifier is required", 400)
            if not state.get("codeChallenge"):
                raise OAuthTokenError("code_challenge is required", 400)
            if state.get("codeChallengeMethod") != "S256":
                raise OAuthTokenError("Invalid code_challenge method", 400)
            digest = hashlib.sha256(verifier.encode()).digest()
            computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
            if computed != state["codeChallenge"]:
                raise OAuthTokenError("Invalid code_verifier", 401)
        else:
            if state.get("codeChallenge"):
                raise OAuthTokenError("code_verifier is required for PKCE flow", 400)

    async def exchange_refresh_token(
        self, client: dict[str, Any], refresh_token: str | None
    ) -> dict[str, Any]:
        from ..access_token import repository as at_repo

        decoded = JwtService().verify(refresh_token)
        if client["clientId"] != decoded.get("clientId"):
            raise OAuthTokenError("Invalid client", 401)
        if client.get("clientSecret") != decoded.get("secret"):
            raise OAuthTokenError("Invalid secret", 401)
        old = await at_repo.get_row(decoded["accessTokenId"])
        if not old:
            raise OAuthTokenError("Invalid access token", 401)
        authorized = await self._is_authorized_row(decoded["clientId"], old["user_id"])
        if not authorized:
            raise OAuthTokenError("Invalid authorized", 401)
        scopes = json.loads(old["scopes"]) if old["scopes"] else []
        new_sign = random_string(16)
        updated = await _rotate_app_token(
            decoded["clientId"], decoded["sign"], client.get("secretId"), new_sign,
            (datetime.now(UTC) + timedelta(
                milliseconds=ms(get_settings().backend_oauth_refresh_token_expire_in)
            )).replace(tzinfo=None),
        )
        if not updated:
            raise OAuthTokenError("Invalid refresh token", 401)
        tokens = await self._issue_access_only(client, old["user_id"], scopes, new_sign)
        return self._token_response(tokens, scopes)

    async def _issue_access_only(
        self, client: dict[str, Any], user_id: str, scopes: list[str], refresh_sign: str
    ) -> dict[str, str]:
        from ..access_token import repository as at_repo

        settings = get_settings()
        token_id = new_id(IdPrefix.ACCESS_TOKEN)
        sign = random_string(16)
        expired = datetime.now(UTC) + timedelta(
            milliseconds=ms(settings.backend_oauth_access_token_expire_in)
        )
        await at_repo.create(
            token_id=token_id,
            name=f"oauth:{client['name']}",
            description=None,
            scopes=json.dumps(scopes),
            space_ids=None,
            base_ids=None,
            user_id=user_id,
            sign=sign,
            client_id=client["clientId"],
            expired_time=expired.replace(tzinfo=None),
            has_full_access=None,
        )
        if client["type"] == "pkce":
            payload = {
                "clientId": client["clientId"],
                "accessTokenId": token_id,
                "sign": refresh_sign,
            }
        else:
            payload = {
                "clientId": client["clientId"],
                "secret": client["clientSecret"],
                "accessTokenId": token_id,
                "sign": refresh_sign,
            }
        refresh_token = JwtService().sign(
            payload, expires_in=settings.backend_oauth_refresh_token_expire_in
        )
        return {"accessToken": get_access_token(token_id, sign), "refreshToken": refresh_token}

    async def _is_authorized_row(self, client_id: str, user_id: str) -> bool:
        async with db_engine.session() as session:
            row = (
                await session.execute(
                    select(OAuthAppAuthorized.id).where(
                        OAuthAppAuthorized.client_id == client_id,
                        OAuthAppAuthorized.user_id == user_id,
                    )
                )
            ).first()
        return row is not None

    async def exchange_device_code(
        self, client: dict[str, Any], device_code: str | None
    ) -> dict[str, Any]:
        if not device_code:
            return {"_rfc_error": "invalid_request", "error_description": "device_code is required"}
        result = await OAuthDeviceService().poll(device_code, client["clientId"])
        if result["status"] != "approved":
            mapping = {
                "pending": "authorization_pending",
                "slow_down": "slow_down",
                "denied": "access_denied",
                "expired": "expired_token",
            }
            return {"_rfc_error": mapping[result["status"]]}
        state = result["state"]
        scopes = state["scopes"]
        try:
            tokens = await self._issue_token_pair(client, state["user"]["id"], scopes)
        except Exception:
            await OAuthDeviceService().restore(device_code, state)
            raise
        return self._token_response(tokens, scopes)
