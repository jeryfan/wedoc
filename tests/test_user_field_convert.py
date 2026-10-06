"""Field type conversion for the user field (convert2User / user cellValue2String):
text -> user resolves tokens against base collaborators by exact id/name/email;
user -> text yields the collaborator title.
"""

from conftest import signup as _signup


async def _create(client, path, body):
    resp = await client.post(path, json=body)
    assert resp.status_code in (200, 201), (path, resp.status_code, resp.text)
    return resp.json()


async def _setup(client):
    email, _ = await _signup(client)
    me = (await client.get("/api/auth/user")).json()
    space = await _create(client, "/api/space", {"name": "S"})
    base = await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"})
    tid = (await _create(client, f"/api/base/{base['id']}/table", {"name": "T"}))["id"]
    return tid, me["id"], me["name"], email


async def _cell(client, tid, rid, fid):
    r = await client.get(f"/api/table/{tid}/record/{rid}?fieldKeyType=id")
    return r.json()["fields"].get(fid)


async def _rec(client, tid, fid, value):
    body = {"fieldKeyType": "id", "records": [{"fields": {fid: value}}]}
    return (await _create(client, f"/api/table/{tid}/record", body))["records"][0]["id"]


async def test_text_to_user_resolves_collaborator(client):
    tid, uid, uname, email = await _setup(client)
    fid = (await _create(client, f"/api/table/{tid}/field",
        {"name": "F", "type": "singleLineText"}))["id"]
    hit = await _rec(client, tid, fid, email)
    miss = await _rec(client, tid, fid, "nobody@example.com")
    await client.put(f"/api/table/{tid}/field/{fid}/convert",
        json={"type": "user", "name": "F", "options": {"isMultiple": False}})
    resolved = await _cell(client, tid, hit, fid)
    assert isinstance(resolved, dict) and resolved["id"] == uid
    assert resolved["title"] == uname
    assert await _cell(client, tid, miss, fid) is None


async def test_user_to_text_yields_title(client):
    tid, uid, uname, _ = await _setup(client)
    fid = (await _create(client, f"/api/table/{tid}/field",
        {"name": "F", "type": "user", "options": {"isMultiple": False}}))["id"]
    rid = await _rec(client, tid, fid, {"id": uid, "title": uname})
    await client.put(f"/api/table/{tid}/field/{fid}/convert",
        json={"type": "singleLineText", "name": "F"})
    assert await _cell(client, tid, rid, fid) == uname
