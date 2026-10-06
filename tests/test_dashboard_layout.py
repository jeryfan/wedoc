"""Dashboard layout number serialization parity.

Layout x/y/w/h echo as integers when whole (JSON.stringify drops the .0), not
1.0/2.0.
"""

from conftest import signup as _signup


async def _dashboard(client):
    await _signup(client)
    sid = (await client.post("/api/space", json={"name": "S"})).json()["id"]
    bid = (await client.post("/api/base", json={"spaceId": sid, "name": "B"})).json()["id"]
    pid = (await client.get("/api/plugin/center/list")).json()[0]["id"]
    did = (await client.post(f"/api/base/{bid}/dashboard", json={"name": "D1"})).json()["id"]
    inst = (
        await client.post(
            f"/api/base/{bid}/dashboard/{did}/plugin", json={"pluginId": pid, "name": "W1"}
        )
    ).json()
    iid = inst.get("pluginInstallId") or inst.get("id")
    return bid, did, iid


async def test_dashboard_layout_coords_are_integers(client):
    bid, did, iid = await _dashboard(client)
    upd = await client.patch(
        f"/api/base/{bid}/dashboard/{did}/layout",
        json={"layout": [{"pluginInstallId": iid, "x": 1, "y": 2, "w": 3, "h": 4}]},
    )
    assert upd.status_code == 200, upd.text
    item = upd.json()["layout"][0]
    assert (item["x"], item["y"], item["w"], item["h"]) == (1, 2, 3, 4)
    assert all(isinstance(item[k], int) for k in ("x", "y", "w", "h"))

    got = await client.get(f"/api/base/{bid}/dashboard/{did}")
    gitem = got.json()["layout"][0]
    assert all(isinstance(gitem[k], int) for k in ("x", "y", "w", "h"))
