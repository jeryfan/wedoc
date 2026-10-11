"""Query-parameter storage auth, validation, response, and cache contract."""

from typing import Any

import httpx
import pytest
from conftest import signup

from wedoc.core.cache import get_cache


async def test_query_params_requires_login_before_validation(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/query-params", json={})
    assert response.status_code == 401
    assert response.json() == {
        "message": "Unauthorized", "status": 401, "code": "unauthorized"
    }


@pytest.mark.parametrize("params", [{}, {"filter": {"x": 1}, "sort": [None, True, "a"]}])
async def test_query_params_stores_unwrapped_values_for_sixty_seconds(
    client: httpx.AsyncClient, params: dict[str, Any]
) -> None:
    await signup(client)
    response = await client.post("/api/query-params", json={"params": params, "extra": 1})
    assert response.status_code == 201, response.text
    assert response.headers["content-type"] == "application/json; charset=utf-8"
    assert set(response.json()) == {"queryId"}
    query_id = response.json()["queryId"]
    assert query_id.startswith("qry")
    cache = get_cache()
    key = f"query-params:{query_id}"
    assert await cache.get(key) == params
    ttl = await cache._redis.pttl(cache.physical_key(key))
    assert 55_000 <= ttl <= 60_000


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({}, 'Invalid input: expected record, received undefined at "params"'),
        ({"params": None}, 'Invalid input: expected record, received null at "params"'),
        ({"params": []}, 'Invalid input: expected record, received array at "params"'),
        ({"params": 1}, 'Invalid input: expected record, received number at "params"'),
        ({"params": "x"}, 'Invalid input: expected record, received string at "params"'),
        ([], "Invalid input: expected object, received array"),
    ],
)
async def test_query_params_validates_payload(
    client: httpx.AsyncClient, payload: Any, message: str
) -> None:
    await signup(client)
    response = await client.post("/api/query-params", json=payload)
    assert response.status_code == 400, response.text
    assert response.json() == {
        "message": f"Validation error: {message}",
        "status": 400,
        "code": "validation_error",
    }


async def test_query_params_empty_body_uses_object_validation(client: httpx.AsyncClient) -> None:
    await signup(client)
    response = await client.post("/api/query-params")
    assert response.status_code == 400, response.text
    assert response.json()["message"] == (
        'Validation error: Invalid input: expected record, received undefined at "params"'
    )


async def test_query_params_rejects_personal_access_token(client: httpx.AsyncClient) -> None:
    await signup(client)
    created = await client.post(
        "/api/access-token",
        json={"name": "query-probe", "scopes": ["table|read"], "expiredTime": "2035-01-01"},
    )
    assert created.status_code == 201, created.text
    client.cookies.clear()
    response = await client.post(
        "/api/query-params", json={"params": {}},
        headers={"Authorization": f"Bearer {created.json()['token']}"},
    )
    assert response.status_code == 403, response.text


async def test_proxy_catch_all_does_not_gain_api_auth_guard(client: httpx.AsyncClient) -> None:
    response = await client.get("/not-an-existing-page")
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"
