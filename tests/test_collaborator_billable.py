"""Collaborator VOs must carry billable (reference parity).

The reference includes `billable: true` on every user collaborator item across
the space list, the space unique list, and the base list. wedoc omitted it.
"""

from conftest import signup as _signup


async def _create(client, path, body):
    resp = await client.post(path, json=body)
    assert resp.status_code in (200, 201), (path, resp.status_code, resp.text)
    return resp.json()


async def test_space_collaborators_list_has_billable(client):
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    sid = space["id"]

    listed = (await client.get(f"/api/space/{sid}/collaborators")).json()
    assert listed["collaborators"], listed
    assert listed["collaborators"][0]["billable"] is True


async def test_space_unique_collaborators_list_has_billable(client):
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    sid = space["id"]

    uniq = (await client.get(f"/api/space/{sid}/collaborators/unique")).json()
    assert uniq["collaborators"], uniq
    assert uniq["collaborators"][0]["billable"] is True


async def test_base_collaborators_list_has_billable(client):
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    base = await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"})

    listed = (await client.get(f"/api/base/{base['id']}/collaborators")).json()
    assert listed["collaborators"], listed
    assert listed["collaborators"][0]["billable"] is True
