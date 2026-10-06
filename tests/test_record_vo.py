"""Record mutation response contract: the write endpoints (create / update /
delete / duplicate / form-submit) echo only the trimmed record shape
``{id, fields}``, while reads (GET one / list) keep the full record VO. A freshly
created record is stamped so ``lastModifiedTime == createdTime`` and
``lastModifiedBy == createdBy`` on reads.

Ports the v2 record DTO used by every write endpoint mapper
(``mapCreateRecordsResultToDto`` / ``mapUpdateRecordResultToDto`` /
``mapDuplicateRecordResultToDto`` / delete via the list DTO read-before-delete),
which is ``{ id, fields }`` with ``fields`` still carrying computed cells, plus
the read-path VO from ``mapTableRecordReadModelToIRecord`` which stamps
last-modified at creation.
"""

from conftest import signup as _signup

_MUTATION_KEYS = {"id", "fields"}


async def _create(client, path, body):
    resp = await client.post(path, json=body)
    assert resp.status_code in (200, 201), (path, resp.status_code, resp.text)
    return resp.json()


async def _setup(client):
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    base = await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"})
    table = await _create(client, f"/api/base/{base['id']}/table", {"name": "T1"})
    resp = await client.get(f"/api/table/{table['id']}/field")
    assert resp.status_code == 200, resp.text
    primary = next(f for f in resp.json() if f.get("isPrimary"))
    return table["id"], primary["id"]


def _assert_mutation_vo(vo):
    assert set(vo) == _MUTATION_KEYS, vo
    assert isinstance(vo["id"], str)
    assert isinstance(vo["fields"], dict)


async def test_create_returns_only_id_and_fields(client):
    tid, primary_id = await _setup(client)
    created = await _create(
        client,
        f"/api/table/{tid}/record",
        {"fieldKeyType": "id", "records": [{"fields": {primary_id: "hello"}}]},
    )
    assert set(created) == {"records"}, created
    vo = created["records"][0]
    _assert_mutation_vo(vo)
    assert vo["fields"][primary_id] == "hello"


async def test_create_stamps_last_modified_equal_to_created(client):
    tid, _primary_id = await _setup(client)
    created = await _create(
        client,
        f"/api/table/{tid}/record",
        {"fieldKeyType": "id", "records": [{"fields": {}}]},
    )
    rid = created["records"][0]["id"]
    vo = (await client.get(f"/api/table/{tid}/record/{rid}?fieldKeyType=id")).json()
    assert "lastModifiedTime" in vo and vo["lastModifiedTime"] == vo["createdTime"]
    assert "lastModifiedBy" in vo and vo["lastModifiedBy"] == vo["createdBy"]


async def test_create_trimmed_fields_carry_computed_cell(client):
    tid, primary_id = await _setup(client)
    formula = await _create(
        client,
        f"/api/table/{tid}/field",
        {"type": "formula", "name": "F", "options": {"expression": f'{{{primary_id}}} & "-x"'}},
    )
    created = await _create(
        client,
        f"/api/table/{tid}/record",
        {"fieldKeyType": "id", "records": [{"fields": {primary_id: "hi"}}]},
    )
    vo = created["records"][0]
    _assert_mutation_vo(vo)
    assert vo["fields"][formula["id"]] == "hi-x"


async def test_patch_single_returns_only_id_and_fields(client):
    tid, primary_id = await _setup(client)
    created = await _create(
        client,
        f"/api/table/{tid}/record",
        {"fieldKeyType": "id", "records": [{"fields": {}}]},
    )
    rid = created["records"][0]["id"]
    resp = await client.patch(
        f"/api/table/{tid}/record/{rid}",
        json={"fieldKeyType": "id", "record": {"fields": {primary_id: "edited"}}},
    )
    assert resp.status_code == 200, resp.text
    vo = resp.json()
    _assert_mutation_vo(vo)
    assert vo["id"] == rid
    assert vo["fields"][primary_id] == "edited"


async def test_patch_bulk_returns_list_of_id_and_fields(client):
    tid, primary_id = await _setup(client)
    created = await _create(
        client,
        f"/api/table/{tid}/record",
        {"fieldKeyType": "id", "records": [{"fields": {}}, {"fields": {}}]},
    )
    ids = [r["id"] for r in created["records"]]
    resp = await client.patch(
        f"/api/table/{tid}/record",
        json={
            "fieldKeyType": "id",
            "records": [
                {"id": rid, "fields": {primary_id: f"v{i}"}} for i, rid in enumerate(ids)
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    vos = resp.json()
    assert isinstance(vos, list) and len(vos) == 2
    assert {v["id"] for v in vos} == set(ids)
    for vo in vos:
        _assert_mutation_vo(vo)


async def test_delete_single_returns_only_id_and_fields(client):
    tid, primary_id = await _setup(client)
    created = await _create(
        client,
        f"/api/table/{tid}/record",
        {"fieldKeyType": "id", "records": [{"fields": {primary_id: "doomed"}}]},
    )
    rid = created["records"][0]["id"]
    resp = await client.delete(f"/api/table/{tid}/record/{rid}")
    assert resp.status_code == 200, resp.text
    vo = resp.json()
    _assert_mutation_vo(vo)
    assert vo["id"] == rid
    assert vo["fields"][primary_id] == "doomed"


async def test_delete_bulk_returns_records_of_id_and_fields(client):
    tid, _primary_id = await _setup(client)
    created = await _create(
        client,
        f"/api/table/{tid}/record",
        {"fieldKeyType": "id", "records": [{"fields": {}}, {"fields": {}}]},
    )
    ids = [r["id"] for r in created["records"]]
    resp = await client.delete(
        f"/api/table/{tid}/record", params=[("recordIds", rid) for rid in ids]
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"records"}, body
    assert {v["id"] for v in body["records"]} == set(ids)
    for vo in body["records"]:
        _assert_mutation_vo(vo)


async def test_duplicate_returns_only_id_and_fields(client):
    tid, primary_id = await _setup(client)
    created = await _create(
        client,
        f"/api/table/{tid}/record",
        {"fieldKeyType": "id", "records": [{"fields": {primary_id: "orig"}}]},
    )
    rid = created["records"][0]["id"]
    resp = await client.post(f"/api/table/{tid}/record/{rid}/duplicate")
    assert resp.status_code == 201, resp.text
    vo = resp.json()
    _assert_mutation_vo(vo)
    assert vo["id"] != rid
    assert vo["fields"][primary_id] == "orig"


async def test_form_submit_returns_only_id_and_fields(client):
    tid, _primary_id = await _setup(client)
    view = await _create(client, f"/api/table/{tid}/view", {"name": "Form", "type": "form"})
    resp = await client.post(
        f"/api/table/{tid}/record/form-submit",
        json={"viewId": view["id"], "fields": {}},
    )
    assert resp.status_code == 201, resp.text
    _assert_mutation_vo(resp.json())


async def test_get_single_and_list_keep_full_vo(client):
    tid, _primary_id = await _setup(client)
    created = await _create(
        client,
        f"/api/table/{tid}/record",
        {"fieldKeyType": "id", "records": [{"fields": {}}]},
    )
    rid = created["records"][0]["id"]
    full = {"id", "fields", "name", "autoNumber", "createdTime", "createdBy",
            "lastModifiedTime", "lastModifiedBy"}
    got = (await client.get(f"/api/table/{tid}/record/{rid}?fieldKeyType=id")).json()
    assert full <= set(got), got
    listed = (await client.get(f"/api/table/{tid}/record?fieldKeyType=id")).json()
    assert full <= set(listed["records"][0]), listed["records"][0]


async def test_last_modified_system_cells_resolve_on_read(client):
    tid, _primary_id = await _setup(client)
    lmt = await _create(client, f"/api/table/{tid}/field", {"type": "lastModifiedTime"})
    lmb = await _create(client, f"/api/table/{tid}/field", {"type": "lastModifiedBy"})
    created = await _create(
        client,
        f"/api/table/{tid}/record",
        {"fieldKeyType": "id", "records": [{"fields": {}}]},
    )
    rid = created["records"][0]["id"]
    vo = (await client.get(f"/api/table/{tid}/record/{rid}?fieldKeyType=id")).json()
    assert vo["fields"][lmt["id"]] == vo["createdTime"]
    assert vo["fields"][lmb["id"]]["id"] == vo["createdBy"]

