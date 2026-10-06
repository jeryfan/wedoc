"""base-node PUT/DELETE parity tests.

Covers the update path for table nodes (rename + icon trim), share cleanup on
node delete, and the folder not-found -> 500 alignment. Ports the update/delete
behaviour of base-node.service.ts.
"""

from conftest import signup as _signup


async def _create(client, path, json):
    resp = await client.post(path, json=json)
    assert resp.status_code in (200, 201), (path, resp.status_code, resp.text)
    return resp.json()


async def _base(client):
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    return await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"})


async def test_update_table_node_renames_table_and_trims_icon(client):
    base = await _base(client)
    node = await _create(
        client, f"/api/base/{base['id']}/node", {"resourceType": "table", "name": "T1"}
    )
    assert node["resourceType"] == "table"
    assert node["resourceMeta"]["name"] == "T1"
    node_id = node["id"]

    resp = await client.put(
        f"/api/base/{base['id']}/node/{node_id}",
        json={"name": "Renamed", "icon": "  star  "},
    )
    assert resp.status_code == 200, resp.text
    vo = resp.json()
    assert vo["resourceType"] == "table"
    assert vo["resourceMeta"]["name"] == "Renamed"
    # icon is z.string().trim(): surrounding whitespace is stripped.
    assert vo["resourceMeta"]["icon"] == "star"

    # a fresh read reflects the persisted rename
    reread = (await client.get(f"/api/base/{base['id']}/node/{node_id}")).json()
    assert reread["resourceMeta"]["name"] == "Renamed"
    assert reread["resourceMeta"]["icon"] == "star"


async def test_delete_table_node_removes_base_share(client, db):
    base = await _base(client)
    node = await _create(
        client, f"/api/base/{base['id']}/node", {"resourceType": "table", "name": "T1"}
    )
    node_id = node["id"]
    await _create(client, f"/api/base/{base['id']}/share", {"nodeId": node_id})

    before = await db.fetchval(
        "SELECT count(*) FROM base_share WHERE base_id = $1 AND node_id = $2",
        base["id"],
        node_id,
    )
    assert before == 1

    resp = await client.delete(f"/api/base/{base['id']}/node/{node_id}")
    assert resp.status_code == 200, resp.text

    after = await db.fetchval(
        "SELECT count(*) FROM base_share WHERE base_id = $1 AND node_id = $2",
        base["id"],
        node_id,
    )
    assert after == 0


async def test_rename_missing_folder_returns_500(client):
    base = await _base(client)
    resp = await client.patch(
        f"/api/base/{base['id']}/node/folder/fldMissing000000000", json={"name": "X"}
    )
    assert resp.status_code == 500, resp.text
    assert resp.json()["code"] == "internal_server_error"


async def test_delete_missing_folder_returns_500(client):
    base = await _base(client)
    resp = await client.delete(f"/api/base/{base['id']}/node/folder/fldMissing000000000")
    assert resp.status_code == 500, resp.text
    assert resp.json()["code"] == "internal_server_error"
