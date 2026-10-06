"""Field-not-found messages differ by endpoint, matching the reference:
convert/plan use "Field {id} not found in table {tableId}" (notFoundInTable),
duplicate/delete use the plain "Field not found" (domainCode not_found).
"""

from conftest import signup as _signup

_MF = "fldZZZZZZZZZZZZZZZZ"


async def _create(client, path, body):
    resp = await client.post(path, json=body)
    assert resp.status_code in (200, 201), (path, resp.status_code, resp.text)
    return resp.json()


async def _table(client):
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    bid = (await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"}))["id"]
    return (await _create(client, f"/api/base/{bid}/table", {"name": "TA"}))["id"]


async def test_convert_missing_field_in_table(client):
    ta = await _table(client)
    resp = await client.put(f"/api/table/{ta}/field/{_MF}/convert",
        json={"name": "X", "type": "singleLineText"})
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["message"] == f"Field {_MF} not found in table {ta}"
    assert body["data"]["localization"]["i18nKey"] == "httpErrors.field.notFoundInTable"
    assert body["data"]["localization"]["context"] == {"tableId": ta, "fieldId": _MF}


async def test_duplicate_missing_field_plain(client):
    ta = await _table(client)
    resp = await client.post(f"/api/table/{ta}/field/{_MF}/duplicate", json={"name": "X"})
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["message"] == "Field not found"
    assert body["data"] == {"domainCode": "not_found", "domainTags": ["not-found"]}
