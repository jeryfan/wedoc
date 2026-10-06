"""v2 orpc envelope + input-validation parity.

The orpc endpoints wrap results as {ok:true,data} / {ok:false,error}; input
errors collect every field issue as "Input validation failed: <path>: <msg>".
"""

from conftest import signup as _signup


async def _base(client):
    await _signup(client)
    sid = (await client.post("/api/space", json={"name": "S"})).json()["id"]
    return (await client.post("/api/base", json={"spaceId": sid, "name": "A"})).json()["id"]


async def test_create_validation_collects_fields(client):
    await _signup(client)
    resp = await client.post("/api/v2/tables/create", json={})
    assert resp.status_code == 400, resp.text
    assert resp.json() == {
        "ok": False,
        "error": (
            "Input validation failed: baseId: Invalid input: expected string, received "
            "undefined; name: Invalid input: expected string, received undefined"
        ),
    }


async def test_delete_records_collects_fields(client):
    await _signup(client)
    resp = await client.request("DELETE", "/api/v2/tables/deleteRecords", json={})
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"] == (
        "Input validation failed: tableId: Invalid input: expected string, received undefined; "
        "recordIds: Invalid input: expected array, received undefined"
    )


async def test_delete_records_empty_list(client):
    await _signup(client)
    resp = await client.request(
        "DELETE", "/api/v2/tables/deleteRecords", json={"tableId": "tblX", "recordIds": []}
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"] == (
        "Input validation failed: recordIds: At least one recordId is required"
    )


async def test_update_records_field_key_type_enum(client):
    await _signup(client)
    resp = await client.post(
        "/api/v2/tables/updateRecords", json={"tableId": "tblX", "fieldKeyType": "bogus"}
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"] == (
        'Input validation failed: fieldKeyType: Invalid option: '
        'expected one of "id"|"name"|"dbFieldName"'
    )


async def test_update_records_requires_target(client):
    await _signup(client)
    resp = await client.post("/api/v2/tables/updateRecords", json={"tableId": "tblX"})
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"] == (
        "Input validation failed: filter: Either records, filter, or recordIds is required"
    )


async def test_create_table_success_envelope(client):
    bid = await _base(client)
    resp = await client.post("/api/v2/tables/create", json={"baseId": bid, "name": "V2T"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert sorted(body["data"].keys()) == ["events", "table"]


async def test_openapi_spec_top_level_keys(client):
    resp = await client.get("/api/v2/openapi.json")
    assert resp.status_code == 200, resp.text
    spec = resp.json()
    assert sorted(spec.keys()) == ["info", "openapi", "paths", "servers"]
    assert spec["openapi"] == "3.1.1"
