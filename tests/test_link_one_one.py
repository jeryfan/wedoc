"""A one-to-one link rejects a second record pointing at an already-linked
target with the reference's 400 (validation.link.one_one_duplicate), on both
create and update.
"""

from conftest import signup as _signup


async def _create(client, path, body):
    resp = await client.post(path, json=body)
    assert resp.status_code in (200, 201), (path, resp.status_code, resp.text)
    return resp.json()


async def _setup(client):
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    bid = (await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"}))["id"]
    ta = (await _create(client, f"/api/base/{bid}/table", {"name": "TA"}))["id"]
    tb = (await _create(client, f"/api/base/{bid}/table", {"name": "TB"}))["id"]
    b1 = (await _create(client, f"/api/table/{tb}/record",
        {"fieldKeyType": "name", "records": [{"fields": {"Name": "b1"}}]}))["records"][0]["id"]
    lf = (await _create(client, f"/api/table/{ta}/field",
        {"name": "Link", "type": "link",
         "options": {"relationship": "oneOne", "foreignTableId": tb}}))["id"]
    return ta, lf, b1


async def _link_record(client, ta, lf, b1):
    return await client.post(f"/api/table/{ta}/record",
        json={"fieldKeyType": "id", "records": [{"fields": {lf: {"id": b1}}}]})


async def test_one_one_duplicate_insert_rejected(client):
    ta, lf, b1 = await _setup(client)
    assert (await _link_record(client, ta, lf, b1)).status_code == 201
    dup = await _link_record(client, ta, lf, b1)
    assert dup.status_code == 400, dup.text
    body = dup.json()
    assert body["message"] == (
        "Cannot complete insert: the target record is already linked "
        "by another record in a one-to-one relationship"
    )
    assert body["data"]["domainCode"] == "validation.link.one_one_duplicate"


async def test_one_one_duplicate_update_rejected(client):
    ta, lf, b1 = await _setup(client)
    assert (await _link_record(client, ta, lf, b1)).status_code == 201
    other = (await _create(client, f"/api/table/{ta}/record",
        {"fieldKeyType": "name", "records": [{"fields": {"Name": "a2"}}]}))["records"][0]["id"]
    up = await client.patch(f"/api/table/{ta}/record/{other}",
        json={"fieldKeyType": "id", "record": {"fields": {lf: {"id": b1}}}})
    assert up.status_code == 400, up.text
    assert "one-to-one relationship" in up.json()["message"]
