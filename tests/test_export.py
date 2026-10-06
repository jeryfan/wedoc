"""Export CSV: a missing table reports the reference's 404 "Invalid tableId"
(before the shared 403 guard), and a real export streams the CSV body."""

from conftest import signup as _signup


async def _create(client, path, body):
    resp = await client.post(path, json=body)
    assert resp.status_code in (200, 201), (path, resp.status_code, resp.text)
    return resp.json()


async def test_export_missing_table_invalid_tableid(client):
    await _signup(client)
    resp = await client.get("/api/export/tblZZZZZZZZZZZZZZZZ")
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["message"] == "Invalid tableId: tblZZZZZZZZZZZZZZZZ"
    assert body["data"]["localization"]["i18nKey"] == "httpErrors.table.notFound"


async def test_export_csv_body(client):
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    bid = (await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"}))["id"]
    ta = (await _create(client, f"/api/base/{bid}/table", {"name": "TA"}))["id"]
    await _create(client, f"/api/table/{ta}/record",
                  {"fieldKeyType": "name", "records": [{"fields": {"Name": "apple"}}]})
    resp = await client.get(f"/api/export/{ta}")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "text/csv; charset=utf-8"
    assert resp.headers["content-disposition"] == "attachment; filename=TA.csv"
    assert resp.text.startswith("\ufeffName,Count,Status\r\n")
    assert "apple" in resp.text
