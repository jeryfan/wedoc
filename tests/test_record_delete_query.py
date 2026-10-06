"""DELETE /record recordIds query parsing parity (qs semantics).

The reference parses the query with qs: the frontend's axios `recordIds[]=`
bracket form is an array (even with one id), a single plain `recordIds=x` is a
scalar and rejected, and an absent key is undefined and rejected.
"""

from conftest import signup as _signup


async def _create(client, path, body):
    resp = await client.post(path, json=body)
    assert resp.status_code in (200, 201), (path, resp.status_code, resp.text)
    return resp.json()


async def _table_with_records(client, n):
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    base = await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"})
    table = await _create(client, f"/api/base/{base['id']}/table", {"name": "T1"})
    tid = table["id"]
    made = await _create(
        client,
        f"/api/table/{tid}/record",
        {"fieldKeyType": "id", "records": [{"fields": {}} for _ in range(n)]},
    )
    return tid, [r["id"] for r in made["records"]]


async def _record_count(client, tid):
    return len((await client.get(f"/api/table/{tid}/record?fieldKeyType=id")).json()["records"])


async def test_delete_records_bracket_array_deletes(client):
    tid, ids = await _table_with_records(client, 2)
    resp = await client.delete(f"/api/table/{tid}/record?recordIds[]={ids[0]}")
    assert resp.status_code == 200, resp.text
    echoed = [r["id"] for r in resp.json()["records"]]
    assert echoed == [ids[0]]
    assert await _record_count(client, tid) == 1


async def test_delete_echo_fields_include_all_fields_null_for_empty(client):
    # unlike create/get/list (which omit empty cells), the delete echo carries
    # an entry for every field, null for empty cells.
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    base = await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"})
    table = await _create(client, f"/api/base/{base['id']}/table", {"name": "T1"})
    tid = table["id"]
    by_name = {f["name"]: f["id"] for f in (await client.get(f"/api/table/{tid}/field")).json()}
    made = await _create(
        client,
        f"/api/table/{tid}/record",
        {"fieldKeyType": "id", "records": [{"fields": {by_name["Name"]: "a"}}]},
    )
    rid = made["records"][0]["id"]

    echo = (await client.delete(f"/api/table/{tid}/record?recordIds[]={rid}")).json()
    fields = echo["records"][0]["fields"]
    assert set(fields) == set(by_name.values())
    assert fields[by_name["Name"]] == "a"
    assert fields[by_name["Count"]] is None
    assert fields[by_name["Status"]] is None


async def test_delete_records_single_scalar_is_400(client):
    tid, ids = await _table_with_records(client, 2)
    resp = await client.delete(f"/api/table/{tid}/record?recordIds={ids[0]}")
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["message"] == (
        'Validation error: Invalid input: expected array, received string at "recordIds"'
    )
    assert body["code"] == "validation_error"
    # nothing deleted
    assert await _record_count(client, tid) == 2


async def test_delete_records_absent_is_400(client):
    tid, _ = await _table_with_records(client, 1)
    resp = await client.delete(f"/api/table/{tid}/record")
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == (
        'Validation error: Invalid input: expected array, received undefined at "recordIds"'
    )


async def test_delete_records_repeated_plain_array_deletes_both(client):
    tid, ids = await _table_with_records(client, 2)
    resp = await client.delete(
        f"/api/table/{tid}/record?recordIds={ids[0]}&recordIds={ids[1]}"
    )
    assert resp.status_code == 200, resp.text
    assert await _record_count(client, tid) == 0
