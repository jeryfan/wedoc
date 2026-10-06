"""Template endpoint parity (non-instance-admin surface).

Instance-admin routes (list all / create / category create) refuse a normal user
with 403 "User is not an admin". category/list is public and empty by default;
permalink of an unknown identifier is 404 "Invalid identifier"; get/visit of a
missing template id hit the reference's unhandled path and answer 500.
"""

from conftest import signup as _signup

_ADMIN_KEY = "httpErrors.permission.userNotAdmin"


async def test_instance_routes_require_admin(client):
    await _signup(client)
    for resp in (
        await client.get("/api/template"),
        await client.post("/api/template/create", json={"name": "X"}),
        await client.post("/api/template/category/create", json={"name": "C"}),
    ):
        assert resp.status_code == 403, resp.text
        body = resp.json()
        assert body["message"] == "User is not an admin"
        assert body["data"]["localization"]["i18nKey"] == _ADMIN_KEY


async def test_category_list_public_empty(client):
    await _signup(client)
    resp = await client.get("/api/template/category/list")
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


async def test_permalink_unknown_is_404(client):
    await _signup(client)
    resp = await client.get("/api/template/permalink/nope")
    assert resp.status_code == 404, resp.text
    assert resp.json()["message"] == "Invalid identifier"


async def test_get_and_visit_missing_template_are_500(client):
    await _signup(client)
    got = await client.get("/api/template/tplZZZZZZZZZZZZZZZZ")
    assert got.status_code == 500, got.text
    visited = await client.patch("/api/template/tplZZZZZZZZZZZZZZZZ/visit")
    assert visited.status_code == 500, visited.text
