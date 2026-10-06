"""Short-link + organization parity.

Enterprise terminology: a missing base share reports "Project share not found";
the enterprise-only artifact type has no backing store here so it resolves to a
404 "Short link target not found" (not the reference's no-resolver 400).
"""

from conftest import signup as _signup


async def test_organization_stubs(client):
    await _signup(client)
    me = await client.get("/api/organization/me")
    assert me.status_code == 200 and me.text == ""
    du = await client.get("/api/organization/department-user")
    assert du.status_code == 200 and du.json() == {"users": [], "total": 0}
    dept = await client.get("/api/organization/department")
    assert dept.status_code == 200 and dept.json() == []


async def test_short_link_missing_body(client):
    await _signup(client)
    resp = await client.post("/api/short-link", json={})
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == (
        'Validation error: Invalid option: expected one of '
        '"view-share"|"base-share"|"template"|"artifact" at "type"; '
        'Invalid input: expected string, received undefined at "resourceId"'
    )


async def test_short_link_base_share_not_found(client):
    await _signup(client)
    resp = await client.post(
        "/api/short-link", json={"type": "base-share", "resourceId": "bseZZZZZZZZZZZZZZZZ"}
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["message"] == "Project share not found"


async def test_short_link_artifact_target_not_found(client):
    await _signup(client)
    resp = await client.post(
        "/api/short-link", json={"type": "artifact", "resourceId": "x"}
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["message"] == "Short link target not found"


async def test_short_link_get_missing(client):
    await _signup(client)
    resp = await client.get("/api/short-link/ZZZZZZZZ")
    assert resp.status_code == 404, resp.text
    assert resp.json()["message"] == "Short link not found"
