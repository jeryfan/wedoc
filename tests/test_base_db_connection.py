"""Base read-only DB connection parity (single-PG, no PUBLIC_DATABASE_PROXY).

Without a database proxy the readonly target is unavailable, so create/retrieve
return an empty body (201/200). A malformed baseId is refused by the permission
guard (403 invalidRequestPath); a missing base is 404 "Project not found".
"""

from conftest import signup as _signup


async def _base(client):
    await _signup(client)
    sid = (await client.post("/api/space", json={"name": "S"})).json()["id"]
    return (await client.post("/api/base", json={"spaceId": sid, "name": "B"})).json()["id"]


async def test_connection_empty_without_proxy(client):
    bid = await _base(client)
    got = await client.get(f"/api/base/{bid}/connection")
    assert got.status_code == 200 and got.content == b"", got.text
    created = await client.post(f"/api/base/{bid}/connection")
    assert created.status_code == 201 and created.content == b"", created.text


async def test_connection_malformed_base_is_403(client):
    await _base(client)
    resp = await client.post("/api/base/badid/connection")
    assert resp.status_code == 403, resp.text
    body = resp.json()
    assert body["message"] == "Request path is not valid"
    assert body["data"]["localization"]["i18nKey"] == "httpErrors.permission.invalidRequestPath"


async def test_connection_missing_base_is_404(client):
    await _base(client)
    for resp in (
        await client.get("/api/base/bseZZZZZZZZZZZZZZZZ/connection"),
        await client.post("/api/base/bseZZZZZZZZZZZZZZZZ/connection"),
    ):
        assert resp.status_code == 404, resp.text
        body = resp.json()
        assert body["message"] == "Project not found"
        assert body["data"]["localization"]["i18nKey"] == "httpErrors.base.notFound"
