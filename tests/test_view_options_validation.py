"""View option validation parity (PATCH /view/:id/options and create).

The reference wraps the body as {options: viewOptionsSchema} (options required)
and validates it against the view's type-specific strict schema: unknown keys and
keys not valid for the view type are rejected with 400 validation_error, both on
create and on the options update.
"""

from conftest import signup as _signup


async def _create(client, path, body):
    resp = await client.post(path, json=body)
    assert resp.status_code in (200, 201), (path, resp.status_code, resp.text)
    return resp.json()


async def _setup(client):
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    base = await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"})
    table = await _create(client, f"/api/base/{base['id']}/table", {"name": "T1"})
    tid = table["id"]
    sel = await _create(client, f"/api/table/{tid}/field",
        {"name": "Sel", "type": "singleSelect", "options": {"choices": [{"name": "A"}]}})
    return tid, sel["id"]


async def test_update_options_requires_wrapper_and_validates_type(client):
    tid, sel = await _setup(client)
    kanban = await _create(client, f"/api/table/{tid}/view", {"name": "K", "type": "kanban"})
    kid = kanban["id"]
    # missing {options:...} wrapper -> 400
    r = await client.patch(f"/api/table/{tid}/view/{kid}/options", json={"stackFieldId": sel})
    assert r.status_code == 400 and r.json().get("code") == "validation_error"
    # unknown key inside options -> 400
    r = await client.patch(f"/api/table/{tid}/view/{kid}/options", json={"options": {"foo": 1}})
    assert r.status_code == 400 and r.json().get("code") == "validation_error"
    # valid kanban options -> 200
    r = await client.patch(f"/api/table/{tid}/view/{kid}/options",
        json={"options": {"stackFieldId": sel, "isCoverFit": True}})
    assert r.status_code == 200
    view = (await client.get(f"/api/table/{tid}/view/{kid}")).json()
    assert view["options"].get("stackFieldId") == sel and view["options"].get("isCoverFit") is True


async def test_gallery_rejects_kanban_only_key(client):
    tid, sel = await _setup(client)
    gallery = await _create(client, f"/api/table/{tid}/view", {"name": "G", "type": "gallery"})
    # stackFieldId is not part of the gallery option schema -> 400
    r = await client.patch(f"/api/table/{tid}/view/{gallery['id']}/options",
        json={"options": {"stackFieldId": sel}})
    assert r.status_code == 400


async def test_create_view_validates_options(client):
    tid, _ = await _setup(client)
    r = await client.post(f"/api/table/{tid}/view",
        json={"name": "K", "type": "kanban", "options": {"foo": 1}})
    assert r.status_code == 400 and r.json().get("code") == "validation_error"
    r = await client.post(f"/api/table/{tid}/view",
        json={"name": "Gr", "type": "grid", "options": {"rowHeight": "short"}})
    assert r.status_code == 201
