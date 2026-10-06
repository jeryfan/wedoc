"""Prefixed-id path params are format-validated: a malformed record/field/view
id yields 400 "Invalid XxxId" (matching the reference id value-objects), while a
well-formed-but-missing id still yields 404.
"""

from conftest import signup as _signup


async def _create(client, path, body):
    resp = await client.post(path, json=body)
    assert resp.status_code in (200, 201), (path, resp.status_code, resp.text)
    return resp.json()


async def _table(client):
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    base = await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"})
    return (await _create(client, f"/api/base/{base['id']}/table", {"name": "T"}))["id"]


async def test_malformed_ids_return_400_invalid(client):
    tid = await _table(client)
    cases = [
        ("GET", f"/api/table/{tid}/record/xyz?fieldKeyType=name", "Invalid RecordId"),
        ("DELETE", f"/api/table/{tid}/record/xyz", "Invalid RecordId"),
        ("GET", f"/api/table/{tid}/field/xyz", "Invalid FieldId"),
        ("DELETE", f"/api/table/{tid}/field/xyz", "Invalid FieldId"),
        ("GET", f"/api/table/{tid}/view/xyz", "Invalid ViewId"),
        ("DELETE", f"/api/table/{tid}/view/xyz", "Invalid ViewId"),
    ]
    for method, path, msg in cases:
        r = await client.request(method, path)
        assert r.status_code == 400, (path, r.status_code, r.text)
        assert r.json()["message"] == msg, (path, r.text)


async def test_wellformed_missing_ids_return_404(client):
    tid = await _table(client)
    r = await client.get(f"/api/table/{tid}/record/recAAAAAAAAAAAAAAAA?fieldKeyType=name")
    assert r.status_code == 404, r.text
    f = await client.get(f"/api/table/{tid}/field/fldAAAAAAAAAAAAAAAA")
    assert f.status_code == 404, f.text
    v = await client.get(f"/api/table/{tid}/view/viwAAAAAAAAAAAAAAAA")
    assert v.status_code == 404, v.text
    assert v.json()["message"] == "View not found: viwAAAAAAAAAAAAAAAA"
