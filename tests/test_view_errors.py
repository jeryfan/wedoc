"""View-not-found and cannot-delete-last errors carry the reference's domainCode
shape, and a delete checks the view exists (404) before the last-view guard.
"""

from conftest import signup as _signup


async def _create(client, path, body):
    resp = await client.post(path, json=body)
    assert resp.status_code in (200, 201), (path, resp.status_code, resp.text)
    return resp.json()


async def _table(client):
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    bid = (await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"}))["id"]
    return (await _create(client, f"/api/base/{bid}/table", {"name": "TA"}))["id"]


async def test_get_missing_view_404(client):
    ta = await _table(client)
    resp = await client.get(f"/api/table/{ta}/view/viwZZZZZZZZZZZZZZZZ")
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["message"] == "View not found: viwZZZZZZZZZZZZZZZZ"
    assert body["data"] == {"domainCode": "view.not_found", "domainTags": ["not-found"]}


async def test_delete_missing_view_checks_existence_first(client):
    # single-view table: a missing id must 404 (existence) before the last-view guard.
    ta = await _table(client)
    resp = await client.delete(f"/api/table/{ta}/view/viwZZZZZZZZZZZZZZZZ")
    assert resp.status_code == 404, resp.text
    assert resp.json()["data"]["domainCode"] == "view.not_found"


async def test_delete_last_view_rejected(client):
    ta = await _table(client)
    only = (await client.get(f"/api/table/{ta}/view")).json()[0]["id"]
    resp = await client.delete(f"/api/table/{ta}/view/{only}")
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["data"] == {"domainCode": "view.cannot_delete_last", "domainTags": ["validation"]}
