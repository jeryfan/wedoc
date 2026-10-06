"""Base-share default-url + not-found wording parity.

The public share default URL for a table node includes the first view id
(/base/{b}/table/{t}/{v}). A missing shareId on the PUBLIC route reports
"Project share not found"; the management routes keep "Base share not found".
"""

from conftest import signup as _signup


async def _shared(client):
    await _signup(client)
    sid = (await client.post("/api/space", json={"name": "S"})).json()["id"]
    bid = (await client.post("/api/base", json={"spaceId": sid, "name": "B"})).json()["id"]
    tid = (await client.post(f"/api/base/{bid}/table", json={"name": "T1"})).json()["id"]
    node = (await client.get(f"/api/base/{bid}/node/list")).json()[0]["id"]
    shid = (await client.post(f"/api/base/{bid}/share", json={"nodeId": node})).json()["shareId"]
    return bid, tid, shid


async def test_public_default_url_has_view(client):
    bid, tid, shid = await _shared(client)
    resp = await client.get(f"/api/share/{shid}/base")
    assert resp.status_code == 200, resp.text
    url = resp.json()["defaultUrl"]
    assert url.startswith(f"/base/{bid}/table/{tid}/")
    assert url.count("/") == 5  # /base/{b}/table/{t}/{v}


async def test_public_missing_share_is_project_wording(client):
    await _signup(client)
    resp = await client.get("/api/share/shrZZZZZZZZZZZZZZZZ/base")
    assert resp.status_code == 404, resp.text
    assert resp.json()["message"] == "Project share not found"


async def test_management_missing_share_keeps_base_wording(client):
    bid, _, _ = await _shared(client)
    resp = await client.request(
        "DELETE", f"/api/base/{bid}/share/shrZZZZZZZZZZZZZZZZ"
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["message"] == "Base share not found"
