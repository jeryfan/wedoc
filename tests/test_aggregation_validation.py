"""Aggregation query-param validation parity.

calendar-daily-collection collects every missing required string into one
"Validation error: ...; ...; ..." message (order startDate, endDate,
startDateFieldId, endDateFieldId). The statistic `field` param is a
partialRecord(enum(StatisticsFunc), string[]): unknown keys give "Invalid key
in record", a scalar value on a valid key gives "expected array, received
string", a bare scalar gives "expected record, received string"; per-entry
issues are collected in query order joined by "; ".
"""

from urllib.parse import quote

from conftest import signup as _signup


async def _table(client):
    await _signup(client)
    sid = (await client.post("/api/space", json={"name": "S"})).json()["id"]
    bid = (await client.post("/api/base", json={"spaceId": sid, "name": "B"})).json()["id"]
    tid = (await client.post(f"/api/base/{bid}/table", json={"name": "T1"})).json()["id"]
    fid = (await client.get(f"/api/table/{tid}/field")).json()[0]["id"]
    return tid, fid


async def test_calendar_collects_all_missing(client):
    tid, _ = await _table(client)
    resp = await client.get(f"/api/table/{tid}/aggregation/calendar-daily-collection")
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == (
        'Validation error: Invalid input: expected string, received undefined at "startDate"; '
        'Invalid input: expected string, received undefined at "endDate"; '
        'Invalid input: expected string, received undefined at "startDateFieldId"; '
        'Invalid input: expected string, received undefined at "endDateFieldId"'
    )


async def test_calendar_partial_missing(client):
    tid, _ = await _table(client)
    resp = await client.get(
        f"/api/table/{tid}/aggregation/calendar-daily-collection?startDate=2026-01-01"
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == (
        'Validation error: Invalid input: expected string, received undefined at "endDate"; '
        'Invalid input: expected string, received undefined at "startDateFieldId"; '
        'Invalid input: expected string, received undefined at "endDateFieldId"'
    )


async def test_field_stat_invalid_key(client):
    tid, _ = await _table(client)
    resp = await client.get(f"/api/table/{tid}/aggregation?field[badkey]=x")
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == 'Validation error: Invalid key in record at "field.badkey"'


async def test_field_stat_valid_key_scalar(client):
    tid, _ = await _table(client)
    resp = await client.get(f"/api/table/{tid}/aggregation?field[count]=x")
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == (
        'Validation error: Invalid input: expected array, received string at "field.count"'
    )


async def test_field_stat_bare_scalar(client):
    tid, _ = await _table(client)
    resp = await client.get(f"/api/table/{tid}/aggregation?field=x")
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == (
        'Validation error: Invalid input: expected record, received string at "field"'
    )


async def test_field_stat_valid_array_ok(client):
    tid, fid = await _table(client)
    resp = await client.get(f"/api/table/{tid}/aggregation?field[count][]={quote(fid)}")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "aggregations": [{"fieldId": fid, "total": {"aggFunc": "count", "value": 0}}]
    }


async def test_field_stat_multi_key_order(client):
    tid, _ = await _table(client)
    resp = await client.get(f"/api/table/{tid}/aggregation?field[count]=y&field[bad1]=x")
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == (
        'Validation error: Invalid input: expected array, received string at "field.count"; '
        'Invalid key in record at "field.bad1"'
    )
