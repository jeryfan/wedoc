"""Base-node id resolution parity.

The reference's node routes resolve a non-BaseNode id in the nodeId slot as a
resourceId (rewrites it to the owning node's id), so GET /node/{tableId} returns
the same node as GET /node/{nodeId}.
"""

from conftest import signup as _signup


async def _base(client):
    await _signup(client)
    sid = (await client.post("/api/space", json={"name": "S"})).json()["id"]
    return (await client.post("/api/base", json={"spaceId": sid, "name": "B"})).json()["id"]


async def test_get_node_by_resource_id_and_node_id(client):
    bid = await _base(client)
    tid = (await client.post(f"/api/base/{bid}/table", json={"name": "T1"})).json()["id"]
    nodes = (await client.get(f"/api/base/{bid}/node/list")).json()
    node_id = nodes[0]["id"]

    by_resource = await client.get(f"/api/base/{bid}/node/{tid}")
    assert by_resource.status_code == 200, by_resource.text
    assert by_resource.json()["resourceId"] == tid
    assert by_resource.json()["id"] == node_id

    by_node = await client.get(f"/api/base/{bid}/node/{node_id}")
    assert by_node.status_code == 200, by_node.text
    assert by_node.json()["id"] == node_id


async def test_get_node_unknown_is_404(client):
    bid = await _base(client)
    resp = await client.get(f"/api/base/{bid}/node/tblZZZZZZZZZZZZZZZZ")
    assert resp.status_code == 404, resp.text
    assert resp.json()["message"] == "Node not found"
