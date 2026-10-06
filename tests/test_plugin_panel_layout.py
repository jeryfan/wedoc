"""Plugin-panel layout number serialization parity.

Layout x/y/w/h are stored as a Double-ish JSON but JSON.stringify renders whole
values without a trailing .0, so the panel VO echoes integer coordinates.
"""

from conftest import signup as _signup


async def _panel(client):
    await _signup(client)
    sid = (await client.post("/api/space", json={"name": "S"})).json()["id"]
    bid = (await client.post("/api/base", json={"spaceId": sid, "name": "B"})).json()["id"]
    tid = (await client.post(f"/api/base/{bid}/table", json={"name": "T1"})).json()["id"]
    pid = (await client.get("/api/plugin/center/list")).json()[0]["id"]
    panel = (await client.post(f"/api/table/{tid}/plugin-panel", json={"name": "P1"})).json()
    inst = (
        await client.post(
            f"/api/table/{tid}/plugin-panel/{panel['id']}/install",
            json={"pluginId": pid, "name": "W1"},
        )
    ).json()
    iid = inst.get("pluginInstallId") or inst.get("id")
    return tid, panel["id"], iid


async def test_layout_coords_are_integers(client):
    tid, pnid, iid = await _panel(client)
    upd = await client.patch(
        f"/api/table/{tid}/plugin-panel/{pnid}/layout",
        json={"layout": [{"pluginInstallId": iid, "x": 1, "y": 2, "w": 3, "h": 4}]},
    )
    assert upd.status_code == 200, upd.text
    item = upd.json()["layout"][0]
    assert (item["x"], item["y"], item["w"], item["h"]) == (1, 2, 3, 4)
    assert all(isinstance(item[k], int) for k in ("x", "y", "w", "h"))

    got = await client.get(f"/api/table/{tid}/plugin-panel/{pnid}")
    gitem = got.json()["layout"][0]
    assert all(isinstance(gitem[k], int) for k in ("x", "y", "w", "h"))
