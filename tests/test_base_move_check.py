"""Base move-check + malformed-resource-path parity.

move-check's query schema requires spaceId: with it, 200; without it the
reference passes undefined downstream and throws an unhandled 500 (title-case
"Internal Server Error"). A missing base still 404s ("Project not found") ahead
of that spaceId check. A malformed resource id on a base route is rejected by
the permission guard with 403 "Request path is not valid" carrying
data.localization.i18nKey = "httpErrors.permission.invalidRequestPath".
"""

from conftest import signup as _signup


async def _base(client):
    await _signup(client)
    sid = (await client.post("/api/space", json={"name": "S"})).json()["id"]
    bid = (await client.post("/api/base", json={"spaceId": sid, "name": "B"})).json()["id"]
    return sid, bid


async def test_move_check_requires_space_id(client):
    sid, bid = await _base(client)
    ok = await client.get(f"/api/base/{bid}/move-check", params={"spaceId": sid})
    assert ok.status_code == 200, ok.text
    assert sorted(ok.json().keys()) == ["affectedFields", "dataDb"]
    assert ok.json()["affectedFields"] == []

    missing = await client.get(f"/api/base/{bid}/move-check")
    assert missing.status_code == 500, missing.text
    body = missing.json()
    assert body["message"] == "Internal Server Error"
    assert body["code"] == "internal_server_error"
    assert "data" not in body


async def test_move_check_missing_base_before_space_id(client):
    await _base(client)
    resp = await client.get("/api/base/bseZZZZZZZZZZZZZZZZ/move-check")
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["message"] == "Project not found"
    assert body["data"]["localization"]["i18nKey"] == "httpErrors.base.notFound"


async def test_malformed_resource_path_is_403_with_localization(client):
    await _base(client)
    resp = await client.get("/api/base/badid/erd")
    assert resp.status_code == 403, resp.text
    body = resp.json()
    assert body["message"] == "Request path is not valid"
    assert body["code"] == "restricted_resource"
    assert body["data"]["localization"]["i18nKey"] == "httpErrors.permission.invalidRequestPath"
