"""Record create/duplicate honor the insert `order` ({viewId, anchorId, position}).

appendRecordOrderIndexes: a created or duplicated record can be positioned in a
view's manual row order relative to an anchor. The row-order column is
materialized lazily and the new row is spaced beyond the anchor toward its next
neighbour; an unknown anchor is a not-found.
"""

from conftest import signup as _signup


async def _create(client, path, body):
    resp = await client.post(path, json=body)
    assert resp.status_code in (200, 201), (path, resp.status_code, resp.text)
    return resp.json()


async def _table(client):
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    base = await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"})
    table = await _create(client, f"/api/base/{base['id']}/table", {"name": "T1"})
    tid = table["id"]
    fields = (await client.get(f"/api/table/{tid}/field")).json()
    name_id = next(f["id"] for f in fields if f.get("isPrimary"))
    vid = (await client.get(f"/api/table/{tid}/view")).json()[0]["id"]
    return tid, name_id, vid


async def _mk(client, tid, name_id, val):
    rec = await _create(
        client,
        f"/api/table/{tid}/record",
        {"fieldKeyType": "id", "records": [{"fields": {name_id: val}}]},
    )
    return rec["records"][0]["id"]


async def _seq(client, tid, name_id, vid):
    lst = (
        await client.get(f"/api/table/{tid}/record?fieldKeyType=id&viewId={vid}&take=100")
    ).json()
    return [r["fields"].get(name_id) for r in lst["records"]]


async def test_create_record_with_order_places_after_anchor(client):
    tid, name_id, vid = await _table(client)
    await _mk(client, tid, name_id, "a")
    r2 = await _mk(client, tid, name_id, "b")
    await _mk(client, tid, name_id, "c")
    await _create(
        client,
        f"/api/table/{tid}/record",
        {
            "fieldKeyType": "id",
            "records": [{"fields": {name_id: "X"}}],
            "order": {"viewId": vid, "anchorId": r2, "position": "after"},
        },
    )
    assert await _seq(client, tid, name_id, vid) == ["a", "b", "X", "c"]


async def test_duplicate_record_with_order_before_anchor(client):
    tid, name_id, vid = await _table(client)
    r1 = await _mk(client, tid, name_id, "a")
    await _mk(client, tid, name_id, "b")
    r3 = await _mk(client, tid, name_id, "c")
    resp = await client.post(
        f"/api/table/{tid}/record/{r1}/duplicate",
        json={"viewId": vid, "anchorId": r3, "position": "before"},
    )
    assert resp.status_code == 201
    assert await _seq(client, tid, name_id, vid) == ["a", "b", "a", "c"]


async def test_duplicate_without_order_appends(client):
    tid, name_id, vid = await _table(client)
    r1 = await _mk(client, tid, name_id, "a")
    await _mk(client, tid, name_id, "b")
    resp = await client.post(f"/api/table/{tid}/record/{r1}/duplicate", json={})
    assert resp.status_code == 201
    assert await _seq(client, tid, name_id, vid) == ["a", "b", "a"]


async def test_order_unknown_anchor_is_not_found(client):
    tid, name_id, vid = await _table(client)
    r1 = await _mk(client, tid, name_id, "a")
    resp = await client.post(
        f"/api/table/{tid}/record/{r1}/duplicate",
        json={"viewId": vid, "anchorId": "recBogus0000000000", "position": "after"},
    )
    assert resp.status_code == 404
    body = resp.json()
    assert body["message"] == "Anchor record not found: recBogus0000000000"
    assert body["code"] == "not_found"
