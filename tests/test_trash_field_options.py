"""Trash resourceMap field-options parity.

The trash listing reads the bare field-options column, where select `choices`
are not stored (they live in a separate store), so a trashed select field's
resourceMap options drop `choices` while keeping other option keys. Non-select
option payloads (number formatting, rating config) pass through unchanged.
"""

from conftest import signup as _signup


async def _table(client):
    await _signup(client)
    sid = (await client.post("/api/space", json={"name": "S"})).json()["id"]
    bid = (await client.post("/api/base", json={"spaceId": sid, "name": "B"})).json()["id"]
    return bid, (await client.post(f"/api/base/{bid}/table", json={"name": "T1"})).json()["id"]


async def _trashed_field_options(client, tid, spec):
    fid = (await client.post(f"/api/table/{tid}/field", json=spec)).json()["id"]
    assert (await client.delete(f"/api/table/{tid}/field/{fid}")).status_code == 200
    items = (
        await client.get(
            "/api/trash/items", params={"resourceType": "table", "resourceId": tid}
        )
    ).json()
    return items["resourceMap"][fid]


async def test_select_trash_options_drop_choices(client):
    _, tid = await _table(client)
    entry = await _trashed_field_options(
        client,
        tid,
        {
            "name": "Sel",
            "type": "singleSelect",
            "options": {"choices": [{"name": "a"}], "preventAutoNewOptions": True},
        },
    )
    assert entry["type"] == "singleSelect"
    assert entry["options"] == {"preventAutoNewOptions": True}


async def test_multiselect_trash_options_become_empty(client):
    _, tid = await _table(client)
    entry = await _trashed_field_options(
        client,
        tid,
        {"name": "Multi", "type": "multipleSelect", "options": {"choices": [{"name": "x"}]}},
    )
    assert entry["options"] == {}


async def test_non_select_trash_options_passthrough(client):
    _, tid = await _table(client)
    entry = await _trashed_field_options(
        client,
        tid,
        {
            "name": "Num",
            "type": "number",
            "options": {"formatting": {"type": "decimal", "precision": 2}},
        },
    )
    assert entry["options"] == {"formatting": {"type": "decimal", "precision": 2}}
