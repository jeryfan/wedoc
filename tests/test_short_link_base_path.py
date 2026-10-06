"""Short-link base-share path parity.

A base-share short link resolves to /share/{shareId} + the base's default node
URL (/base/{b}/table/{t}/{v}); a missing base share reports a bare
"Project share not found" (no localization data, unlike the base-share route).
"""

from conftest import signup as _signup


async def _base_share(client):
    await _signup(client)
    sid = (await client.post("/api/space", json={"name": "S"})).json()["id"]
    bid = (await client.post("/api/base", json={"spaceId": sid, "name": "B"})).json()["id"]
    tid = (await client.post(f"/api/base/{bid}/table", json={"name": "T1"})).json()["id"]
    node = (await client.get(f"/api/base/{bid}/node/list")).json()[0]["id"]
    shid = (await client.post(f"/api/base/{bid}/share", json={"nodeId": node})).json()["shareId"]
    return bid, tid, shid


async def test_base_share_short_link_path_has_default_node(client):
    bid, tid, shid = await _base_share(client)
    resp = await client.post(
        "/api/short-link", json={"type": "base-share", "resourceId": shid}
    )
    assert resp.status_code == 201, resp.text
    path = resp.json()["path"]
    assert path.startswith(f"/share/{shid}/base/{bid}/table/{tid}/")
    code = resp.json()["code"]
    got = await client.get(f"/api/short-link/{code}")
    assert got.json()["path"] == path


async def test_base_share_short_link_missing_is_bare(client):
    await _signup(client)
    resp = await client.post(
        "/api/short-link", json={"type": "base-share", "resourceId": "shrZZZZZZZZZZZZZZZZ"}
    )
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["message"] == "Project share not found"
    assert "data" not in body
