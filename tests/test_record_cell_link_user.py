"""Write-time cell validation for the structured field types (typecast off):
link/user/attachment reject malformed shapes with the reference's zod message,
a link to a missing record 400s (FK -> validation.link.invalid_reference), and a
user cell referencing a non-collaborator 400s with ``User(<id>) not found``.
"""

from conftest import signup as _signup


async def _create(client, path, body):
    resp = await client.post(path, json=body)
    assert resp.status_code in (200, 201), (path, resp.status_code, resp.text)
    return resp.json()


async def _setup(client):
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    bid = (await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"}))["id"]
    ta = (await _create(client, f"/api/base/{bid}/table", {"name": "TA"}))["id"]
    tb = (await _create(client, f"/api/base/{bid}/table", {"name": "TB"}))["id"]
    link = (await _create(client, f"/api/table/{ta}/field",
        {"name": "Link", "type": "link",
         "options": {"relationship": "manyOne", "foreignTableId": tb}}))["id"]
    usr = (await _create(client, f"/api/table/{ta}/field",
        {"name": "Usr", "type": "user", "options": {"isMultiple": False}}))["id"]
    att = (await _create(client, f"/api/table/{ta}/field",
        {"name": "Att", "type": "attachment"}))["id"]
    return ta, {"link": link, "usr": usr, "att": att}


async def _write(client, ta, fid, value):
    return await client.post(f"/api/table/{ta}/record",
        json={"fieldKeyType": "id", "records": [{"fields": {fid: value}}]})


async def test_link_malformed_shape_rejected(client):
    ta, f = await _setup(client)
    for bad in ("notanobject", 123):
        resp = await _write(client, ta, f["link"], bad)
        assert resp.status_code == 400, resp.text
        assert resp.json()["message"] == 'Invalid value for field "Link": \u2716 Invalid input'


async def test_link_missing_record_rejected(client):
    ta, f = await _setup(client)
    resp = await _write(client, ta, f["link"], {"id": "recZZZZZZZZZZZZZZZZ"})
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["message"] == "Cannot complete insert: a linked record does not exist"
    assert body["data"]["domainCode"] == "validation.link.invalid_reference"


async def test_user_malformed_shape_rejected(client):
    ta, f = await _setup(client)
    resp = await _write(client, ta, f["usr"], "notanobject")
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == (
        'Invalid value for field "Usr": \u2716 Invalid input: expected object, received string'
    )


async def test_user_not_collaborator_rejected(client):
    ta, f = await _setup(client)
    resp = await _write(client, ta, f["usr"], {"id": "usrZZZZZZZZZZZZZZZZ", "title": "x"})
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["message"] == "User(usrZZZZZZZZZZZZZZZZ) not found"
    assert body["data"]["domainCode"] == "validation.field.user_not_found"


async def test_attachment_malformed_shape_rejected(client):
    ta, f = await _setup(client)
    resp = await _write(client, ta, f["att"], "notanobject")
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == (
        'Invalid value for field "Att": \u2716 Invalid input: expected array, received string'
    )


async def test_manymany_missing_record_rejected(client):
    # ManyMany writes the junction after the host row commits (separate txn); a
    # missing target is pre-checked so it 400s the way the host-FK relationships
    # do instead of orphaning the row with a 500.
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    bid = (await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"}))["id"]
    tb = (await _create(client, f"/api/base/{bid}/table", {"name": "TB"}))["id"]
    ta = (await _create(client, f"/api/base/{bid}/table", {"name": "TA"}))["id"]
    lf = (await _create(client, f"/api/table/{ta}/field",
        {"name": "Link", "type": "link",
         "options": {"relationship": "manyMany", "foreignTableId": tb}}))["id"]
    resp = await _write(client, ta, lf, [{"id": "recZZZZZZZZZZZZZZZZ"}])
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["message"] == "Cannot complete insert: a linked record does not exist"
    assert body["data"]["domainCode"] == "validation.link.invalid_reference"
