"""GET base VO must expose enabledAuthority/restrictedAuthority (reference parity).

The reference's single-base GET response carries `enabledAuthority` and
`restrictedAuthority` (both false for a base without an authority matrix). The
base-list response does not; only the single GET does.
"""

from conftest import signup as _signup


async def _create(client, path, body):
    resp = await client.post(path, json=body)
    assert resp.status_code in (200, 201), (path, resp.status_code, resp.text)
    return resp.json()


async def test_get_base_vo_includes_authority_flags(client):
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    base = await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"})

    got = (await client.get(f"/api/base/{base['id']}")).json()
    assert got["enabledAuthority"] is False
    assert got["restrictedAuthority"] is False


async def test_base_list_omits_authority_flags(client):
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"})

    listed = (await client.get("/api/base/access/all")).json()
    assert listed, "expected at least one accessible base"
    assert "enabledAuthority" not in listed[0]
    assert "restrictedAuthority" not in listed[0]
