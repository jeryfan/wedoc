"""Duplicating a record with a link cell re-establishes the copy's relation:
manyOne/manyMany keep the link (201), one-to-one 400s (target already linked).
"""

from conftest import signup as _signup


async def _create(client, path, body):
    resp = await client.post(path, json=body)
    assert resp.status_code in (200, 201), (path, resp.status_code, resp.text)
    return resp.json()


async def _setup(client, relationship):
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    bid = (await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"}))["id"]
    tb = (await _create(client, f"/api/base/{bid}/table", {"name": "TB"}))["id"]
    b1 = (await _create(client, f"/api/table/{tb}/record",
        {"fieldKeyType": "name", "records": [{"fields": {"Name": "b1"}}]}))["records"][0]["id"]
    ta = (await _create(client, f"/api/base/{bid}/table", {"name": "TA"}))["id"]
    lf = (await _create(client, f"/api/table/{ta}/field",
        {"name": "Link", "type": "link",
         "options": {"relationship": relationship, "foreignTableId": tb}}))["id"]
    a1 = (await _create(client, f"/api/table/{ta}/record",
        {"fieldKeyType": "id", "records": [{"fields": {lf: {"id": b1}}}]}))["records"][0]["id"]
    return ta, lf, a1


async def test_duplicate_many_one_keeps_link(client):
    ta, lf, a1 = await _setup(client, "manyOne")
    resp = await client.post(f"/api/table/{ta}/record/{a1}/duplicate", json={})
    assert resp.status_code == 201, resp.text
    assert resp.json()["fields"][lf]["title"] == "b1"


async def test_duplicate_many_many_keeps_link(client):
    ta, lf, a1 = await _setup(client, "manyMany")
    resp = await client.post(f"/api/table/{ta}/record/{a1}/duplicate", json={})
    assert resp.status_code == 201, resp.text
    assert [x["title"] for x in resp.json()["fields"][lf]] == ["b1"]


async def test_duplicate_one_one_rejected(client):
    ta, _lf, a1 = await _setup(client, "oneOne")
    resp = await client.post(f"/api/table/{ta}/record/{a1}/duplicate", json={})
    assert resp.status_code == 400, resp.text
    assert "one-to-one relationship" in resp.json()["message"]
