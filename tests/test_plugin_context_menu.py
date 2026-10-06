"""Plugin context-menu install/list/get/rename/delete parity.

Ports plugin-context-menu.controller.ts: install requires pluginId, the install
VO is { name, order, pluginInstallId }, and a missing install id reports 400
"Plugin install not found".
"""

from conftest import signup as _signup


async def _table(client):
    await _signup(client)
    sid = (await client.post("/api/space", json={"name": "S"})).json()["id"]
    bid = (await client.post("/api/base", json={"spaceId": sid, "name": "B"})).json()["id"]
    return (await client.post(f"/api/base/{bid}/table", json={"name": "T"})).json()["id"]


async def _published_plugin_id(client):
    center = (await client.get("/api/plugin/center/list")).json()
    return center[0]["id"] if center else None


async def test_install_requires_plugin_id(client):
    tid = await _table(client)
    resp = await client.post(f"/api/table/{tid}/plugin-context-menu/install", json={})
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == (
        'Validation error: Invalid input: expected string, received undefined at "pluginId"'
    )


async def test_get_missing_install(client):
    tid = await _table(client)
    resp = await client.get(f"/api/table/{tid}/plugin-context-menu/piuZZZZZZZZZZZZZZZZ")
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == "Plugin install not found"


async def test_install_and_list(client):
    tid = await _table(client)
    pid = await _published_plugin_id(client)
    assert pid, "no published plugin available"
    inst = await client.post(
        f"/api/table/{tid}/plugin-context-menu/install", json={"pluginId": pid, "name": "CM"}
    )
    assert inst.status_code == 201, inst.text
    assert sorted(inst.json().keys()) == ["name", "order", "pluginInstallId"]
    iid = inst.json()["pluginInstallId"]

    listed = await client.get(f"/api/table/{tid}/plugin-context-menu")
    assert listed.status_code == 200, listed.text
    assert any(item.get("pluginInstallId") == iid for item in listed.json())

    renamed = await client.patch(
        f"/api/table/{tid}/plugin-context-menu/{iid}/rename", json={"name": "CM2"}
    )
    assert renamed.status_code == 200, renamed.text

    deleted = await client.delete(f"/api/table/{tid}/plugin-context-menu/{iid}")
    assert deleted.status_code == 200, deleted.text
