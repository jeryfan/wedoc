"""Table delete -> base trash -> restore brings the table back.

Regression for the restore path: a deleted table is marked provision_state
"deleting"; restore must reset it to "ready" (and clear deleted_time), otherwise
the base's table list (which requires provision_state == "ready") keeps hiding it
even though restore reported success.
"""

from conftest import signup as _signup


async def _create(client, path, body):
    resp = await client.post(path, json=body)
    assert resp.status_code in (200, 201), (path, resp.status_code, resp.text)
    return resp.json()


async def test_table_restore_brings_table_back(client):
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    base = await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"})
    bid = base["id"]
    table = await _create(client, f"/api/base/{bid}/table", {"name": "T"})
    tid = table["id"]

    # delete the table
    d = await client.delete(f"/api/base/{bid}/table/{tid}")
    assert d.status_code == 200, d.text
    assert all(t["id"] != tid for t in (await client.get(f"/api/base/{bid}/table")).json())

    # it surfaces as a base trash item
    items = (await client.get(
        "/api/trash/items", params={"resourceId": bid, "resourceType": "base"}
    )).json()
    trash_items = items.get("trashItems") or []
    assert any(it["resourceType"] == "table" for it in trash_items), items
    trash_id = next(it["id"] for it in trash_items if it["resourceType"] == "table")

    # restore -> 201 and the table is live again
    r = await client.post(f"/api/trash/restore/{trash_id}")
    assert r.status_code == 201, r.text
    tables = (await client.get(f"/api/base/{bid}/table")).json()
    assert any(t["id"] == tid for t in tables), [t["id"] for t in tables]


async def test_record_restore_reinserts_from_snapshot(client):
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    base = await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"})
    bid = base["id"]
    tid = (await _create(client, f"/api/base/{bid}/table", {"name": "T"}))["id"]
    rid = (await _create(client, f"/api/table/{tid}/record",
        {"fieldKeyType": "name", "records": [{"fields": {"Name": "keep", "Count": 42}}]}
    ))["records"][0]["id"]

    # delete the record -> it surfaces as a table trash "record" item
    d = await client.delete(f"/api/table/{tid}/record/{rid}")
    assert d.status_code == 200, d.text
    items = (await client.get("/api/trash/items",
        params={"resourceId": tid, "resourceType": "table", "resourceTypes": "record"})).json()
    trash_id = items["trashItems"][0]["id"]

    # restore -> 201, the original record id + field values are re-inserted
    r = await client.post(f"/api/trash/restore/{trash_id}", params={"tableId": tid})
    assert r.status_code == 201, r.text
    recs = (await client.get(f"/api/table/{tid}/record",
        params={"fieldKeyType": "name", "take": "10"})).json()["records"]
    restored = next((rec for rec in recs if rec["id"] == rid), None)
    assert restored is not None, [rec["id"] for rec in recs]
    assert restored["fields"].get("Name") == "keep"
    assert restored["fields"].get("Count") == 42
    # the record trash item is cleared after restore
    after = (await client.get("/api/trash/items",
        params={"resourceId": tid, "resourceType": "table", "resourceTypes": "record"})).json()
    assert after.get("trashItems", []) == []
