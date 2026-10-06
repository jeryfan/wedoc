"""Base permission-map parity: the base owner grant includes the routine|*
action family (enterprise role map), alongside automation|*.
"""

from conftest import signup as _signup


async def test_base_permission_includes_routine(client):
    await _signup(client)
    sid = (await client.post("/api/space", json={"name": "S"})).json()["id"]
    bid = (await client.post("/api/base", json={"spaceId": sid, "name": "B"})).json()["id"]
    perm = await client.get(f"/api/base/{bid}/permission")
    assert perm.status_code == 200, perm.text
    body = perm.json()
    for action in ("routine|create", "routine|delete", "routine|read", "routine|update"):
        assert body.get(action) is True, action
    # sanity: the automation family the routine map mirrors is present too
    assert body["automation|create"] is True
    assert body["table|create"] is True
