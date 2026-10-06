"""Personal access-token CRUD parity (create/list/get/update/refresh/delete).

Ports access-token.controller.ts: the created token shape, the zod field
validations (name/scopes min, scope enum, expiredTime date), and the
create→get→update→refresh→delete lifecycle.
"""

from conftest import signup as _signup


async def test_create_shape(client):
    await _signup(client)
    resp = await client.post(
        "/api/access-token",
        json={"name": "T", "scopes": ["table|read"], "expiredTime": "2030-01-01"},
    )
    assert resp.status_code == 201, resp.text
    assert sorted(resp.json().keys()) == [
        "createdTime",
        "expiredTime",
        "id",
        "name",
        "scopes",
        "token",
    ]


async def test_create_empty_scopes(client):
    await _signup(client)
    resp = await client.post(
        "/api/access-token", json={"name": "T", "scopes": [], "expiredTime": "2030-01-01"}
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == (
        'Validation error: Too small: expected array to have >=1 items at "scopes"'
    )


async def test_create_bad_expired(client):
    await _signup(client)
    resp = await client.post(
        "/api/access-token",
        json={"name": "T", "scopes": ["table|read"], "expiredTime": "notadate"},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == 'Validation error: ExpiredTime: Invalid Date  at "expiredTime"'


async def test_create_bad_scope(client):
    await _signup(client)
    resp = await client.post(
        "/api/access-token",
        json={"name": "T", "scopes": ["bogus|x"], "expiredTime": "2030-01-01"},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"].startswith(
        'Validation error: Invalid option: expected one of "space|create"'
    )


async def test_lifecycle(client):
    await _signup(client)
    created = await client.post(
        "/api/access-token",
        json={
            "name": "T",
            "description": "d",
            "scopes": ["table|read", "record|read"],
            "expiredTime": "2030-01-01",
        },
    )
    assert created.status_code == 201, created.text
    tid = created.json()["id"]

    got = await client.get(f"/api/access-token/{tid}")
    assert got.status_code == 200, got.text
    assert got.json()["scopes"] == ["table|read", "record|read"]

    updated = await client.put(
        f"/api/access-token/{tid}", json={"name": "T2", "scopes": ["table|read"]}
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "T2"
    assert updated.json()["scopes"] == ["table|read"]

    refreshed = await client.post(
        f"/api/access-token/{tid}/refresh", json={"expiredTime": "2031-01-01"}
    )
    assert refreshed.status_code == 200, refreshed.text
    assert "token" in refreshed.json()

    deleted = await client.delete(f"/api/access-token/{tid}")
    assert deleted.status_code == 200, deleted.text
