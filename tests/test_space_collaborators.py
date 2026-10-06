"""Space collaborators parity: list VO shape + the sole-owner guards.

The signed-in creator is the space's only owner: downgrading or removing them is
refused with a localized message. The list VO carries total + uniqTotal.
"""

from conftest import signup as _signup


async def _space_owner(client):
    await _signup(client)
    sid = (await client.post("/api/space", json={"name": "S"})).json()["id"]
    uid = (await client.get("/api/auth/user")).json()["id"]
    return sid, uid


async def test_space_collaborators_list_shape(client):
    sid, uid = await _space_owner(client)
    resp = await client.get(f"/api/space/{sid}/collaborators")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert sorted(body.keys()) == ["collaborators", "total", "uniqTotal"]
    assert body["total"] == 1 and body["uniqTotal"] == 1
    entry = body["collaborators"][0]
    assert entry["role"] == "owner"
    assert entry["type"] == "user"
    assert entry["userId"] == uid


async def test_cannot_downgrade_only_owner(client):
    sid, uid = await _space_owner(client)
    resp = await client.patch(
        f"/api/space/{sid}/collaborators",
        json={"principalId": uid, "principalType": "user", "role": "editor"},
    )
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["message"] == "Cannot change the role of the only owner of the space"
    assert body["data"]["localization"]["i18nKey"] == "httpErrors.space.cannotChangeOnlyOwnerRole"


async def test_cannot_delete_only_owner(client):
    sid, uid = await _space_owner(client)
    resp = await client.request(
        "DELETE",
        f"/api/space/{sid}/collaborators",
        params={"principalId": uid, "principalType": "user"},
    )
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["message"] == "Cannot delete the only owner of the space"
    assert body["data"]["localization"]["i18nKey"] == "httpErrors.space.cannotDeleteOnlyOwner"
