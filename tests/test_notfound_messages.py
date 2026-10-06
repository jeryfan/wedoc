"""Not-found messages for base and base-scoped table match the reference: a
missing base reads "Project not found"; a missing table under a base reads
"Table {id} not found in project {baseId}".
"""

from conftest import signup as _signup


async def _create(client, path, body):
    resp = await client.post(path, json=body)
    assert resp.status_code in (200, 201), (path, resp.status_code, resp.text)
    return resp.json()


async def _base(client):
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    return (await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"}))["id"]


async def test_missing_base_reads_project_not_found(client):
    await _signup(client)
    resp = await client.get("/api/base/bseZZZZZZZZZZZZZZZZ")
    assert resp.status_code == 404, resp.text
    assert resp.json()["message"] == "Project not found"


async def test_missing_table_under_base(client):
    bid = await _base(client)
    resp = await client.get(f"/api/base/{bid}/table/tblZZZZZZZZZZZZZZZZ")
    assert resp.status_code == 404, resp.text
    assert resp.json()["message"] == f"Table tblZZZZZZZZZZZZZZZZ not found in project {bid}"


async def test_missing_table_update_name(client):
    bid = await _base(client)
    resp = await client.put(f"/api/base/{bid}/table/tblZZZZZZZZZZZZZZZZ/name",
        json={"name": "X"})
    assert resp.status_code == 404, resp.text
    assert resp.json()["message"] == f"Table tblZZZZZZZZZZZZZZZZ not found in project {bid}"
