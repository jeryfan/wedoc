"""Base-node list/tree serialize resourceMeta users with a full avatar URL and
an integer order (matching the reference's JS number serialization)."""

from conftest import signup as _signup


async def _create(client, path, body):
    resp = await client.post(path, json=body)
    assert resp.status_code in (200, 201), (path, resp.status_code, resp.text)
    return resp.json()


async def test_node_list_avatar_full_url_and_int_order(client):
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    bid = (await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"}))["id"]
    await _create(client, f"/api/base/{bid}/table", {"name": "TA"})
    nodes = (await client.get(f"/api/base/{bid}/node/list")).json()
    assert len(nodes) == 1
    node = nodes[0]
    # order is a whole number serialized as int (not 1.0)
    assert node["order"] == 1
    assert isinstance(node["order"], int)
    user = node["resourceMeta"]["createdByUser"]
    # avatar is the full public storage URL, not the raw relative path
    assert user["avatar"].startswith("http")
    assert "/api/attachments/read/public/avatar/" in user["avatar"]
