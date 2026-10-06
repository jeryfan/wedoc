"""View creation compensates columnMeta visibility and type-specific options.

viewDataCompensation (view.service): kanban/gallery/calendar mark the primary
field visible; gallery defaults coverFieldId to the first attachment field;
calendar defaults start/end date field ids from date fields; form marks every
non-computed, non-button field visible. Plugin views require an options object
carrying pluginId and are routed through the plugin-install path.
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


async def _primary_id(client, tid):
    fields = (await client.get(f"/api/table/{tid}/field")).json()
    return next(f["id"] for f in fields if f.get("isPrimary"))


async def test_gallery_primary_visible_and_options_present(client):
    tid = await _table(client)
    primary = await _primary_id(client, tid)
    view = await _create(client, f"/api/table/{tid}/view", {"name": "G", "type": "gallery"})
    assert view["columnMeta"][primary]["visible"] is True
    # options is always present for gallery (empty object when no attachment field)
    assert view["options"] == {}


async def test_gallery_cover_defaults_to_attachment_field(client):
    tid = await _table(client)
    att = await _create(client, f"/api/table/{tid}/field", {"name": "Att", "type": "attachment"})
    view = await _create(client, f"/api/table/{tid}/view", {"name": "G", "type": "gallery"})
    assert view["options"]["coverFieldId"] == att["id"]


async def test_calendar_primary_visible_and_date_options(client):
    tid = await _table(client)
    primary = await _primary_id(client, tid)
    due = await _create(client, f"/api/table/{tid}/field", {"name": "Due", "type": "date"})
    view = await _create(client, f"/api/table/{tid}/view", {"name": "C", "type": "calendar"})
    assert view["columnMeta"][primary]["visible"] is True
    assert view["options"]["startDateFieldId"] == due["id"]
    assert view["options"]["endDateFieldId"] == due["id"]


async def test_form_skips_computed_fields(client):
    tid = await _table(client)
    primary = await _primary_id(client, tid)
    fx = await _create(
        client,
        f"/api/table/{tid}/field",
        {"name": "Fx", "type": "formula", "options": {"expression": "1"}},
    )
    view = await _create(client, f"/api/table/{tid}/view", {"name": "F", "type": "form"})
    assert view["columnMeta"][primary]["visible"] is True
    assert "visible" not in view["columnMeta"][fx["id"]]


async def test_plugin_view_requires_options(client):
    tid = await _table(client)
    resp = await client.post(f"/api/table/{tid}/view", json={"name": "P", "type": "plugin"})
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "validation_error"
    assert body["message"] == (
        'Validation error: Invalid input: expected object, received undefined at "options"'
    )


async def test_plugin_view_unknown_plugin_is_not_found(client):
    tid = await _table(client)
    resp = await client.post(
        f"/api/table/{tid}/view",
        json={
            "name": "P",
            "type": "plugin",
            "options": {
                "pluginId": "plgMissing000000000",
                "pluginInstallId": "pli000000000000000",
                "pluginLogo": "http://logo",
            },
        },
    )
    assert resp.status_code == 404
    assert resp.json()["message"] == "Plugin not found with id: plgMissing000000000"
