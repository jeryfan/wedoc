"""Successful social callbacks must open the same session as password login."""

from unittest.mock import AsyncMock

import httpx
import pytest
from conftest import signup
from starlette.requests import Request

from wedoc.core.cache import get_cache
from wedoc.modules.auth import social


@pytest.mark.parametrize("provider", ["github", "google", "oidc"])
@pytest.mark.parametrize("redirect_uri", [None, "/base/example"])
async def test_social_callback_opens_a_usable_session(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    redirect_uri: str | None,
) -> None:
    email, created = await signup(client)
    user = created.json()
    client.cookies.clear()
    monkeypatch.setattr(
        social, "_exchange_and_fetch_profile",
        AsyncMock(return_value={"id": user["id"], "email": email, "name": user["name"]}),
    )
    query = b"code=unused"
    if redirect_uri:
        state = user["id"]
        await get_cache().set_detail(f"oauth2:state:{state}", {"redirectUri": redirect_uri}, 60)
        query += f"&state={state}".encode()
    request = Request({
        "type": "http", "method": "GET", "scheme": "http",
        "path": f"/api/auth/{provider}/callback", "query_string": query,
        "headers": [], "server": ("test", 80),
    })
    response = await social._callback(provider, request)
    assert response.status_code == 302
    assert response.headers["location"] == (redirect_uri or "/")
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("auth_session=")
    me = await client.get("/api/auth/user/me", headers={"Cookie": cookie.split(";", 1)[0]})
    assert me.status_code == 200
    assert me.json()["id"] == user["id"]
    assert me.json()["email"] == email
