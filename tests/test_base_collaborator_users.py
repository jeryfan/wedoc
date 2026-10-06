"""Base collaborators/users VO parity.

GET /api/base/:baseId/collaborators/users returns each user with a snake_case
`created_time` (the collaborator row's created time, ISO string) alongside
id/name/email/avatar.
"""

from conftest import signup as _signup


async def _base(client):
    await _signup(client)
    sid = (await client.post("/api/space", json={"name": "S"})).json()["id"]
    return (await client.post("/api/base", json={"spaceId": sid, "name": "B"})).json()["id"]


async def test_collaborator_users_include_created_time(client):
    bid = await _base(client)
    resp = await client.get(f"/api/base/{bid}/collaborators/users")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    user = body["users"][0]
    assert sorted(user.keys()) == ["avatar", "created_time", "email", "id", "name"]
    assert user["created_time"].endswith("Z")
