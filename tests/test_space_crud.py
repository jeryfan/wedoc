"""Space CRUD parity: create/get/list/update shapes and the not-found (403,
"space <id> is not found") envelope.
"""

from conftest import signup as _signup


async def test_create_space_shape(client):
    await _signup(client)
    resp = await client.post("/api/space", json={"name": "S1"})
    assert resp.status_code == 201, resp.text
    assert sorted(resp.json().keys()) == ["id", "name"]
    assert resp.json()["name"] == "S1"


async def test_get_space_missing(client):
    await _signup(client)
    resp = await client.get("/api/space/spcZZZZZZZZZZZZZZZZ")
    assert resp.status_code == 403, resp.text
    assert resp.json()["message"] == "space spcZZZZZZZZZZZZZZZZ is not found"


async def test_update_space_name(client):
    await _signup(client)
    sid = (await client.post("/api/space", json={"name": "S2"})).json()["id"]
    resp = await client.patch(f"/api/space/{sid}", json={"name": "S2x"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "S2x"


async def test_list_spaces(client):
    await _signup(client)
    await client.post("/api/space", json={"name": "S1"})
    resp = await client.get("/api/space")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, list) and body
    assert all(sorted(item.keys()) == ["avatar", "id", "name", "role"] for item in body)
