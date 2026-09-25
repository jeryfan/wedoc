"""Social login (github/google/oidc), registered per SOCIAL_AUTH_PROVIDERS.

Mirrors the upstream ConditionalModule gating: with no providers configured
these paths are never registered and fall through to the standard 404
(``Cannot GET /api/auth/github``) — the deployed baseline verified against ref.

When a provider IS configured, ``GET /api/auth/{provider}`` redirects to the
provider's authorization endpoint and ``/callback`` exchanges the code, resolves
the account, opens a session and redirects. The provider round-trip needs real
OAuth client credentials + reachable provider endpoints, so the configured path
is not exercised by the parity harness (external verification pending
credentials); the unconfigured 404 contract is the verified surface.
"""

import os
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Request, Response
from starlette.responses import RedirectResponse

from ...config import Settings
from ...core.cache import get_cache
from ...core.ids import random_string

_AUTHORIZE_TTL = 600


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    return value or None


def _provider_config(provider: str) -> dict[str, Any]:
    p = provider.upper()
    cfg: dict[str, Any] = {
        "clientID": _env(f"BACKEND_{p}_CLIENT_ID"),
        "clientSecret": _env(f"BACKEND_{p}_CLIENT_SECRET"),
        "callbackURL": _env(f"BACKEND_{p}_CALLBACK_URL"),
    }
    if provider == "oidc":
        cfg.update(
            {
                "authorizationURL": _env("BACKEND_OIDC_AUTHORIZATION_URL"),
                "tokenURL": _env("BACKEND_OIDC_TOKEN_URL"),
                "userInfoURL": _env("BACKEND_OIDC_USER_INFO_URL"),
            }
        )
    return cfg


_AUTHORIZE_URLS = {
    "github": "https://github.com/login/oauth/authorize",
    "google": "https://accounts.google.com/o/oauth2/v2/auth",
}
_SCOPES = {
    "github": "user:email",
    "google": "profile email",
    "oidc": "openid profile email",
}


def social_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/auth")

    for provider in settings.social_providers:
        if provider not in ("github", "google", "oidc"):
            continue
        _register(router, provider)
    return router


def _register(router: APIRouter, provider: str) -> None:
    async def authenticate(request: Request) -> Response:
        return await _authorize(provider, request)

    async def callback(request: Request) -> Response:
        return await _callback(provider, request)

    router.add_api_route(f"/{provider}", authenticate, methods=["GET"])
    router.add_api_route(f"/{provider}/callback", callback, methods=["GET"])


async def _authorize(provider: str, request: Request) -> Response:
    # SocialGuard: an explicit access_denied bounces back to the login page.
    if request.query_params.get("error") == "access_denied":
        return RedirectResponse("/auth/login", status_code=302)
    cfg = _provider_config(provider)
    authorize_url = (
        cfg.get("authorizationURL") if provider == "oidc" else _AUTHORIZE_URLS[provider]
    )
    state = random_string(24)
    redirect_uri = request.query_params.get("redirectUri")
    await get_cache().set_detail(
        f"oauth2:state:{state}", {"redirectUri": redirect_uri}, _AUTHORIZE_TTL
    )
    params = {
        "client_id": cfg.get("clientID") or "",
        "redirect_uri": cfg.get("callbackURL") or "",
        "scope": _SCOPES[provider],
        "state": state,
        "response_type": "code",
    }
    return RedirectResponse(f"{authorize_url}?{urlencode(params)}", status_code=302)


async def _callback(provider: str, request: Request) -> Response:
    if request.query_params.get("error") == "access_denied":
        return RedirectResponse("/auth/login", status_code=302)
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    cached = await get_cache().get(f"oauth2:state:{state}") if state else None
    redirect_uri = (cached or {}).get("redirectUri") if isinstance(cached, dict) else None

    profile = await _exchange_and_fetch_profile(provider, code)
    user = await _find_or_create_user(provider, profile)

    from ...core.security.auth import session_login
    from ..user.service import UserService

    await UserService().refresh_last_sign_time(user["id"])
    response = RedirectResponse(
        redirect_uri if _is_valid_redirect_path(redirect_uri) else "/", status_code=302
    )
    await session_login(request, response, user["id"])
    return response


def _is_valid_redirect_path(path: str | None) -> bool:
    if not path:
        return False
    # Same-origin relative path only (mirrors controller.adapter.isValidRedirectPath).
    return path.startswith("/") and not path.startswith("//")


async def _exchange_and_fetch_profile(provider: str, code: str | None) -> dict[str, Any]:
    cfg = _provider_config(provider)
    async with httpx.AsyncClient(timeout=30.0) as client:
        if provider == "github":
            token = await _post_token(
                client,
                "https://github.com/login/oauth/access_token",
                {
                    "client_id": cfg.get("clientID"),
                    "client_secret": cfg.get("clientSecret"),
                    "code": code,
                    "redirect_uri": cfg.get("callbackURL"),
                },
                headers={"Accept": "application/json"},
            )
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            me = (await client.get("https://api.github.com/user", headers=headers)).json()
            emails_resp = await client.get(
                "https://api.github.com/user/emails", headers=headers
            )
            email = _primary_email(emails_resp.json()) or me.get("email")
            return {
                "id": str(me.get("id")),
                "email": email,
                "name": me.get("name") or me.get("login"),
                "avatarUrl": me.get("avatar_url"),
            }
        if provider == "google":
            token = await _post_token(
                client,
                "https://oauth2.googleapis.com/token",
                {
                    "client_id": cfg.get("clientID"),
                    "client_secret": cfg.get("clientSecret"),
                    "code": code,
                    "redirect_uri": cfg.get("callbackURL"),
                    "grant_type": "authorization_code",
                },
            )
            me = (
                await client.get(
                    "https://www.googleapis.com/oauth2/v2/userinfo",
                    headers={"Authorization": f"Bearer {token}"},
                )
            ).json()
            return {
                "id": str(me.get("id")),
                "email": me.get("email"),
                "name": me.get("name"),
                "avatarUrl": me.get("picture"),
            }
        token = await _post_token(
            client,
            cfg.get("tokenURL") or "",
            {
                "client_id": cfg.get("clientID"),
                "client_secret": cfg.get("clientSecret"),
                "code": code,
                "redirect_uri": cfg.get("callbackURL"),
                "grant_type": "authorization_code",
            },
        )
        me = (
            await client.get(
                cfg.get("userInfoURL") or "",
                headers={"Authorization": f"Bearer {token}"},
            )
        ).json()
        return {
            "id": str(me.get("sub") or me.get("id")),
            "email": me.get("email"),
            "name": me.get("name") or me.get("preferred_username"),
            "avatarUrl": me.get("picture"),
        }


async def _post_token(
    client: httpx.AsyncClient, url: str, data: dict[str, Any], headers: dict[str, str] | None = None
) -> str | None:
    resp = await client.post(url, data=data, headers=headers or {"Accept": "application/json"})
    try:
        body = resp.json()
    except ValueError:
        return None
    return body.get("access_token") if isinstance(body, dict) else None


def _primary_email(emails: Any) -> str | None:
    if not isinstance(emails, list):
        return None
    for entry in emails:
        if isinstance(entry, dict) and entry.get("primary") and entry.get("email"):
            return entry["email"]
    for entry in emails:
        if isinstance(entry, dict) and entry.get("email"):
            return entry["email"]
    return None


async def _find_or_create_user(provider: str, profile: dict[str, Any]) -> dict[str, Any]:
    from ...core.errors import ApiError, HttpErrorCode
    from ..user.service import UserService

    email = profile.get("email")
    if not email:
        raise ApiError(
            f"No email provided from {provider.capitalize()}", HttpErrorCode.UNAUTHORIZED
        )
    users = UserService()
    existing = await users.get_user_by_email(email)
    if existing is not None:
        if existing.get("deactivated_time"):
            raise ApiError(
                "Your account has been deactivated by the administrator",
                HttpErrorCode.VALIDATION_ERROR,
            )
        return existing
    return await users.create_user_with_setting_check(
        {"email": email, "name": profile.get("name"), "avatar": profile.get("avatarUrl")},
        account={
            "provider": provider,
            "provider_id": profile.get("id") or "",
            "type": "oauth",
        },
    )
