"""Space invitation-link + collaborator parity.

Ports the space invitation/collaborator controllers: invitation-link CRUD, the
role enum (owner|creator|editor|commenter|viewer), the email min-length, and the
collaborator list envelope { collaborators, total, uniqTotal }.
"""

from conftest import signup as _signup

_ROLE_MSG = (
    'Validation error: Invalid option: expected one of '
    '"owner"|"creator"|"editor"|"commenter"|"viewer" at "role"'
)


async def _space(client):
    await _signup(client)
    return (await client.post("/api/space", json={"name": "S"})).json()["id"]


async def test_invitation_link_create_shape(client):
    sid = await _space(client)
    resp = await client.post(f"/api/space/{sid}/invitation/link", json={"role": "editor"})
    assert resp.status_code == 201, resp.text
    assert sorted(resp.json().keys()) == [
        "createdBy",
        "createdTime",
        "invitationCode",
        "invitationId",
        "inviteUrl",
        "role",
    ]
    assert resp.json()["role"] == "editor"


async def test_invitation_link_bad_role(client):
    sid = await _space(client)
    resp = await client.post(f"/api/space/{sid}/invitation/link", json={"role": "superadmin"})
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == _ROLE_MSG


async def test_invitation_link_missing_role(client):
    sid = await _space(client)
    resp = await client.post(f"/api/space/{sid}/invitation/link", json={})
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == _ROLE_MSG


async def test_email_invitation_empty(client):
    sid = await _space(client)
    resp = await client.post(
        f"/api/space/{sid}/invitation/email", json={"emails": [], "role": "editor"}
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == (
        'Validation error: Too small: expected array to have >=1 items at "emails"'
    )


async def test_collaborator_list_envelope(client):
    sid = await _space(client)
    resp = await client.get(f"/api/space/{sid}/collaborators")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert sorted(body.keys()) == ["collaborators", "total", "uniqTotal"]
    assert body["total"] == 1
    owner = body["collaborators"][0]
    assert owner["role"] == "owner"
    assert owner["resourceType"] == "space"
    assert owner["type"] == "user"


async def test_invitation_link_update_and_delete(client):
    sid = await _space(client)
    created = await client.post(f"/api/space/{sid}/invitation/link", json={"role": "viewer"})
    iid = created.json()["invitationId"]
    updated = await client.patch(
        f"/api/space/{sid}/invitation/link/{iid}", json={"role": "commenter"}
    )
    assert updated.status_code == 200, updated.text
    bad = await client.patch(
        f"/api/space/{sid}/invitation/link/{iid}", json={"role": "bogus"}
    )
    assert bad.status_code == 400, bad.text
    assert bad.json()["message"] == _ROLE_MSG
    deleted = await client.delete(f"/api/space/{sid}/invitation/link/{iid}")
    assert deleted.status_code == 200, deleted.text
