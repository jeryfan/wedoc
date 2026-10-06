"""Field options.defaultValue is applied on record create for omitted fields
(appendDefaultValue / getDefaultValue), and only when the field key is missing.
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
    return (await _create(client, f"/api/base/{base['id']}/table", {"name": "T"}))["id"]


async def _field(client, tid, body):
    return (await _create(client, f"/api/table/{tid}/field", body))["id"]


async def test_defaults_applied_on_create_for_missing_fields(client):
    tid = await _setup(client)
    txt = await _field(client, tid, {"name": "Txt", "type": "singleLineText",
        "options": {"defaultValue": "hi"}})
    num = await _field(client, tid, {"name": "Num", "type": "number",
        "options": {"defaultValue": 5}})
    chk = await _field(client, tid, {"name": "Chk", "type": "checkbox",
        "options": {"defaultValue": True}})
    ssel = await _field(client, tid, {"name": "Sel", "type": "singleSelect",
        "options": {"choices": [{"name": "a"}, {"name": "b"}], "defaultValue": "a"}})

    rid = (await _create(client, f"/api/table/{tid}/record",
        {"fieldKeyType": "id", "records": [{"fields": {}}]}))["records"][0]["id"]
    cells = (await client.get(f"/api/table/{tid}/record/{rid}?fieldKeyType=id")).json()["fields"]
    assert cells.get(txt) == "hi"
    assert cells.get(num) == 5
    assert cells.get(chk) is True
    assert cells.get(ssel) == "a"


async def test_default_not_applied_when_field_present(client):
    tid = await _setup(client)
    txt = await _field(client, tid, {"name": "Txt", "type": "singleLineText",
        "options": {"defaultValue": "hi"}})
    # explicit value (even empty string, which stores as null) suppresses the default
    rid = (await _create(client, f"/api/table/{tid}/record",
        {"fieldKeyType": "id", "records": [{"fields": {txt: "custom"}}]}))["records"][0]["id"]
    cells = (await client.get(f"/api/table/{tid}/record/{rid}?fieldKeyType=id")).json()["fields"]
    assert cells.get(txt) == "custom"
