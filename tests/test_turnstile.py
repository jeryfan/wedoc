"""Enabled challenge checks must precede authentication and mail side effects."""

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from conftest import signup, uniq_email

from wedoc import config


def enable_challenge(monkeypatch: pytest.MonkeyPatch, *, enabled: bool = True) -> None:
    settings = config.get_settings().model_copy(update={
        "turnstile_site_key": "test-site",
        "turnstile_secret_key": "test-secret" if enabled else None,
    })
    settings.__dict__.pop("is_turnstile_enabled", None)
    monkeypatch.setattr(config, "_settings", settings)


def mock_provider(
    monkeypatch: pytest.MonkeyPatch,
    replies: list[httpx.Response | Exception],
) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://challenges.cloudflare.com/turnstile/v0/siteverify"
        assert request.method == "POST"
        requests.append(json.loads(request.content))
        reply = replies[min(len(requests) - 1, len(replies) - 1)]
        if isinstance(reply, Exception):
            raise reply
        return reply

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handle), **kwargs),
    )
    return requests


@pytest.mark.parametrize("path", ["signup", "send-signup-verification-code"])
async def test_challenge_missing_rejected_before_signup_or_mail(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    enable_challenge(monkeypatch)
    requests = mock_provider(monkeypatch, [httpx.Response(200, json={"success": True})])
    response = await client.post(
        f"/api/auth/{path}", json={"email": uniq_email(), "password": "ProbePass123!"}
    )
    assert response.status_code == 400
    assert response.json() == {
        "message": "Turnstile token is required", "status": 400, "code": "validation_error"
    }
    assert requests == []


async def test_signin_challenge_failure_uses_existing_credentials_error(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    email, _ = await signup(client)
    client.cookies.clear()
    enable_challenge(monkeypatch)
    response = await client.post(
        "/api/auth/signin", json={"email": email, "password": "Passw0rd!1"}
    )
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_credentials"
    assert response.json()["message"] == "Email or password is incorrect"


@pytest.mark.parametrize("path", ["signup", "send-signup-verification-code", "signin"])
async def test_valid_challenge_is_forwarded_before_success(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    email = uniq_email()
    if path == "signin":
        email, _ = await signup(client)
        client.cookies.clear()
    enable_challenge(monkeypatch)
    requests = mock_provider(monkeypatch, [httpx.Response(200, json={"success": True})])
    response = await client.post(
        f"/api/auth/{path}",
        json={"email": email, "password": "Passw0rd!1", "turnstileToken": "test-response"},
    )
    assert response.status_code == (201 if path == "signup" else 200)
    assert requests == [{
        "secret": "test-secret", "response": "test-response", "remoteip": "127.0.0.1"
    }]


@pytest.mark.parametrize("token", ["x" * 2049, "😀" * 1025], ids=["ascii", "utf16"])
async def test_oversized_challenge_rejected_without_network(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, token: str
) -> None:
    enable_challenge(monkeypatch)
    requests = mock_provider(monkeypatch, [httpx.Response(200, json={"success": True})])
    response = await client.post(
        "/api/auth/signup",
        json={"email": uniq_email(), "password": "ProbePass123!", "turnstileToken": token},
    )
    assert response.status_code == 400
    assert response.json()["message"] == "Invalid verification token"
    assert requests == []


@pytest.mark.parametrize(
    ("replies", "attempts", "message"),
    [
        ([httpx.Response(200, json={"success": False})], 1,
         "Verification failed. Please refresh and try again."),
        ([httpx.Response(503)], 3,
         "Verification service temporarily unavailable. Please try again."),
        ([httpx.Response(200, content=b"invalid json")], 3,
         "Verification service temporarily unavailable. Please try again."),
        ([httpx.ConnectError("offline")], 3,
         "Verification service temporarily unavailable. Please try again."),
        ([httpx.Response(503), httpx.Response(200, json={"success": True})], 2, None),
    ],
)
async def test_challenge_failures_retry_only_transient_errors(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    replies: list[httpx.Response | Exception],
    attempts: int,
    message: str | None,
) -> None:
    enable_challenge(monkeypatch)
    requests = mock_provider(monkeypatch, replies)
    sleep = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep)
    response = await client.post(
        "/api/auth/signup",
        json={
            "email": uniq_email(), "password": "ProbePass123!", "turnstileToken": "test-response"
        },
    )
    if message:
        assert response.status_code == 400
        assert response.json() == {"message": message, "status": 400, "code": "validation_error"}
    else:
        assert response.status_code == 201
    assert len(requests) == attempts
    assert [call.args[0] for call in sleep.await_args_list] == [1, 2][:attempts - 1]


async def test_provider_redirect_does_not_consume_retry(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    enable_challenge(monkeypatch)
    requests = mock_provider(monkeypatch, [
        httpx.Response(307, headers={
            "Location": "https://challenges.cloudflare.com/turnstile/v0/siteverify"
        }),
        httpx.Response(200, json={"success": True}),
    ])
    sleep = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep)
    response = await client.post(
        "/api/auth/signup",
        json={"email": uniq_email(), "password": "ProbePass123!", "turnstileToken": "redirect"},
    )
    assert response.status_code == 201
    assert len(requests) == 2
    sleep.assert_not_awaited()


@pytest.mark.parametrize("enabled", [False, True])
async def test_public_challenge_key_requires_both_configuration_values(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    enable_challenge(monkeypatch, enabled=enabled)
    response = await client.get("/api/admin/setting/public")
    assert response.status_code == 200
    assert response.json()["turnstileSiteKey"] == ("test-site" if enabled else None)
