"""Response content-type + export view-type error parity.

Every JSON response carries `application/json; charset=utf-8` (NestJS/Express
default). Exporting a non-grid / missing view 400s; a missing view has no
viewType, so the localized context is empty (JS omits an undefined key).
"""

from conftest import signup as _signup


async def _table(client):
    await _signup(client)
    sid = (await client.post("/api/space", json={"name": "S"})).json()["id"]
    bid = (await client.post("/api/base", json={"spaceId": sid, "name": "B"})).json()["id"]
    return (await client.post(f"/api/base/{bid}/table", json={"name": "T1"})).json()["id"]


async def test_json_responses_have_charset(client):
    await _signup(client)
    ok = await client.get("/api/space")
    assert ok.headers["content-type"] == "application/json; charset=utf-8"
    missing = await client.get("/api/space/spcZZZZZZZZZZZZZZZZ")
    assert missing.headers["content-type"] == "application/json; charset=utf-8"


async def test_export_missing_view_empty_context(client):
    tid = await _table(client)
    resp = await client.get(f"/api/export/{tid}", params={"viewId": "viwZZZZZZZZZZZZZZZZ"})
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["message"] == "undefined is not support to export"
    loc = body["data"]["localization"]
    assert loc["i18nKey"] == "httpErrors.export.notSupportViewType"
    assert loc["context"] == {}


async def test_export_grid_ok_is_csv(client):
    tid = await _table(client)
    resp = await client.get(f"/api/export/{tid}")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "text/csv; charset=utf-8"
    assert resp.headers["content-disposition"].startswith("attachment; filename=")
