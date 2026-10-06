"""v1 link-integrity parity.

A healthy base reports no link issues (hasIssues false, empty list) and the
fix endpoint returns an empty array. wedoc provisions link fields atomically,
so the deep scan the reference performs has nothing to flag.
"""

from conftest import signup as _signup


async def _base(client):
    await _signup(client)
    sid = (await client.post("/api/space", json={"name": "S"})).json()["id"]
    return (await client.post("/api/base", json={"spaceId": sid, "name": "A"})).json()["id"]


async def test_link_check_healthy_base(client):
    bid = await _base(client)
    resp = await client.get(f"/api/integrity/base/{bid}/link-check")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"hasIssues": False, "linkFieldIssues": []}


async def test_link_fix_healthy_base(client):
    bid = await _base(client)
    resp = await client.post(f"/api/integrity/base/{bid}/link-fix")
    assert resp.status_code == 201, resp.text
    assert resp.json() == []
