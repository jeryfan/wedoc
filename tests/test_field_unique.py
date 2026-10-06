"""A field marked unique rejects duplicate non-null values on record create and
update with the reference's 400 (Cannot complete insert/update: field X must
have a unique value); multiple empty cells stay allowed.
"""

from conftest import signup as _signup


async def _create(client, path, body):
    resp = await client.post(path, json=body)
    assert resp.status_code in (200, 201), (path, resp.status_code, resp.text)
    return resp.json()


async def _setup(client):
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    base = await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"})
    tid = (await _create(client, f"/api/base/{base['id']}/table", {"name": "T"}))["id"]
    fid = (await _create(client, f"/api/table/{tid}/field",
        {"name": "U", "type": "singleLineText", "unique": True}))["id"]
    return tid, fid


async def _rec(client, tid, fid, value):
    fields = {fid: value} if value is not None else {}
    body = {"fieldKeyType": "id", "records": [{"fields": fields}]}
    resp = await client.post(f"/api/table/{tid}/record", json=body)
    return resp


async def test_duplicate_insert_rejected(client):
    tid, fid = await _setup(client)
    assert (await _rec(client, tid, fid, "x")).status_code == 201
    dup = await _rec(client, tid, fid, "x")
    assert dup.status_code == 400, dup.text
    body = dup.json()
    assert body["message"] == f"Cannot complete insert: field {fid} must have a unique value"
    assert body["data"]["domainCode"] == "validation.field.unique"


async def test_duplicate_update_rejected(client):
    tid, fid = await _setup(client)
    await _rec(client, tid, fid, "x")
    other = (await _rec(client, tid, fid, "y")).json()["records"][0]["id"]
    up = await client.patch(f"/api/table/{tid}/record/{other}",
        json={"fieldKeyType": "id", "record": {"fields": {fid: "x"}}})
    assert up.status_code == 400, up.text
    assert up.json()["message"] == f"Cannot complete update: field {fid} must have a unique value"


async def test_multiple_empty_values_allowed(client):
    tid, fid = await _setup(client)
    assert (await _rec(client, tid, fid, None)).status_code == 201
    assert (await _rec(client, tid, fid, None)).status_code == 201
