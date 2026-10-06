"""Record list query-param validation parity.

Ports getRecordsRoSchema: take/skip bounds, and the fieldKeyType / cellFormat
enums (a bad value 400s with the reference's "Error <field>, You should set it
to ..." message). Issues are collected in field order: cellFormat, fieldKeyType,
take, skip.
"""

from conftest import signup as _signup

_FKT_MSG = (
    'Validation error: Error fieldKeyType, You should set it to '
    '"name" or "id" or "dbFieldName" at "fieldKeyType"'
)
_CF_MSG = (
    'Validation error: Error cellFormat, You should set it to "json" or "text" at "cellFormat"'
)


async def _table(client):
    await _signup(client)
    sid = (await client.post("/api/space", json={"name": "S"})).json()["id"]
    bid = (await client.post("/api/base", json={"spaceId": sid, "name": "A"})).json()["id"]
    return (await client.post(f"/api/base/{bid}/table", json={"name": "TA"})).json()["id"]


async def test_field_key_type_bad(client):
    tid = await _table(client)
    resp = await client.get(f"/api/table/{tid}/record", params={"fieldKeyType": "bogus"})
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == _FKT_MSG


async def test_field_key_type_valid(client):
    tid = await _table(client)
    for v in ("name", "id", "dbFieldName"):
        resp = await client.get(f"/api/table/{tid}/record", params={"fieldKeyType": v})
        assert resp.status_code == 200, (v, resp.text)


async def test_cell_format_bad(client):
    tid = await _table(client)
    resp = await client.get(f"/api/table/{tid}/record", params={"cellFormat": "bogus"})
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == _CF_MSG


async def test_take_bounds(client):
    tid = await _table(client)
    too_big = await client.get(f"/api/table/{tid}/record", params={"take": "5000"})
    assert too_big.status_code == 400
    assert too_big.json()["message"] == (
        'Validation error: Can\'t take more than 1000 records, please reduce take count at "take"'
    )
    zero = await client.get(f"/api/table/{tid}/record", params={"take": "0"})
    assert zero.status_code == 400
    assert zero.json()["message"] == 'Validation error: You should at least take 1 record at "take"'


async def test_combined_errors_field_order(client):
    tid = await _table(client)
    resp = await client.get(
        f"/api/table/{tid}/record",
        params={"take": "0", "fieldKeyType": "bogus", "cellFormat": "bad", "skip": "-1"},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == (
        'Validation error: Error cellFormat, You should set it to "json" or "text" at '
        '"cellFormat"; Error fieldKeyType, You should set it to "name" or "id" or '
        '"dbFieldName" at "fieldKeyType"; You should at least take 1 record at "take"; '
        'You can not skip a negative count of records at "skip"'
    )


async def _record(client):
    tid = await _table(client)
    created = await client.post(
        f"/api/table/{tid}/record",
        json={"fieldKeyType": "name", "records": [{"fields": {"Name": "x"}}]},
    )
    return tid, created.json()["records"][0]["id"]


async def test_get_record_field_key_type_bad(client):
    tid, rid = await _record(client)
    resp = await client.get(f"/api/table/{tid}/record/{rid}", params={"fieldKeyType": "bogus"})
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == _FKT_MSG


async def test_get_record_cell_format_bad(client):
    tid, rid = await _record(client)
    resp = await client.get(f"/api/table/{tid}/record/{rid}", params={"cellFormat": "bogus"})
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == _CF_MSG


async def test_get_record_valid(client):
    tid, rid = await _record(client)
    resp = await client.get(f"/api/table/{tid}/record/{rid}", params={"fieldKeyType": "id"})
    assert resp.status_code == 200, resp.text
