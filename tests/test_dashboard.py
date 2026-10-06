"""Dashboard + widget CRUD parity.

Ports dashboard.controller.ts: dashboard create/get/rename/delete, the
"name required" validation and "Dashboard not found" 404, and the plugin-widget
install VO { id, name, pluginId, pluginInstallId }.
"""

from conftest import signup as _signup


async def _base(client):
    await _signup(client)
    sid = (await client.post("/api/space", json={"name": "S"})).json()["id"]
    return (await client.post("/api/base", json={"spaceId": sid, "name": "B"})).json()["id"]


async def _published_plugin_id(client):
    return (await client.get("/api/plugin/center/list")).json()[0]["id"]


async def test_create_dashboard_shape(client):
    bid = await _base(client)
    resp = await client.post(f"/api/base/{bid}/dashboard", json={"name": "D1"})
    assert resp.status_code == 201, resp.text
    assert sorted(resp.json().keys()) == ["id", "name"]


async def test_create_dashboard_requires_name(client):
    bid = await _base(client)
    resp = await client.post(f"/api/base/{bid}/dashboard", json={})
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == (
        'Validation error: Invalid input: expected string, received undefined at "name"'
    )


async def test_get_dashboard_missing(client):
    bid = await _base(client)
    resp = await client.get(f"/api/base/{bid}/dashboard/dshZZZZZZZZZZZZZZZZ")
    assert resp.status_code == 404, resp.text
    assert resp.json()["message"] == "Dashboard not found"


async def test_widget_install_lifecycle(client):
    bid = await _base(client)
    pid = await _published_plugin_id(client)
    did = (await client.post(f"/api/base/{bid}/dashboard", json={"name": "D1"})).json()["id"]
    inst = await client.post(
        f"/api/base/{bid}/dashboard/{did}/plugin", json={"pluginId": pid, "name": "W1"}
    )
    assert inst.status_code == 201, inst.text
    assert sorted(inst.json().keys()) == ["id", "name", "pluginId", "pluginInstallId"]
    iid = inst.json()["pluginInstallId"]

    renamed = await client.patch(
        f"/api/base/{bid}/dashboard/{did}/plugin/{iid}/rename", json={"name": "W2"}
    )
    assert renamed.status_code == 200, renamed.text
    deleted = await client.delete(f"/api/base/{bid}/dashboard/{did}/plugin/{iid}")
    assert deleted.status_code == 200, deleted.text
