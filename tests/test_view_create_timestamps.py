"""View creation stamps lastModified equal to created, matching the reference.

The reference stamps a view's __last_modified_time/__last_modified_by at
creation (equal to the created values), so a freshly created view — the
default grid view from table creation and any POST-created view — reports
createdTime == lastModifiedTime and a present lastModifiedBy.
"""

from conftest import signup as _signup


async def _create(client, path, body):
    resp = await client.post(path, json=body)
    assert resp.status_code in (200, 201), (path, resp.status_code, resp.text)
    return resp.json()


async def _table(client):
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    base = await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"})
    table = await _create(client, f"/api/base/{base['id']}/table", {"name": "T1"})
    return table["id"]


async def test_default_view_created_time_equals_last_modified_time(client):
    tid = await _table(client)
    view = (await client.get(f"/api/table/{tid}/view")).json()[0]
    assert view["createdTime"] == view["lastModifiedTime"]
    assert view["lastModifiedBy"] == view["createdBy"]


async def test_created_view_stamps_last_modified_equal_to_created(client):
    tid = await _table(client)
    view = await _create(client, f"/api/table/{tid}/view", {"name": "V2", "type": "grid"})
    assert "lastModifiedTime" in view
    assert "lastModifiedBy" in view
    assert view["createdTime"] == view["lastModifiedTime"]
    assert view["lastModifiedBy"] == view["createdBy"]
