"""Record status VO parity.

GET /api/table/{tableId}/record/{recordId}/status returns {isDeleted, isVisible}:
an existing record is {false, true}; a missing record {true, false}; a record
excluded by the query filter is {false, false}.
"""

import json

from conftest import signup as _signup


async def _record(client):
    await _signup(client)
    sid = (await client.post("/api/space", json={"name": "S"})).json()["id"]
    bid = (await client.post("/api/base", json={"spaceId": sid, "name": "B"})).json()["id"]
    tid = (await client.post(f"/api/base/{bid}/table", json={"name": "T1"})).json()["id"]
    fld = (await client.get(f"/api/table/{tid}/field")).json()[0]
    rid = (
        await client.post(
            f"/api/table/{tid}/record",
            json={"records": [{"fields": {fld["name"]: "x"}}], "fieldKeyType": "name"},
        )
    ).json()["records"][0]["id"]
    return tid, rid, fld["id"]


async def test_status_existing(client):
    tid, rid, _ = await _record(client)
    resp = await client.get(f"/api/table/{tid}/record/{rid}/status")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"isDeleted": False, "isVisible": True}


async def test_status_missing(client):
    tid, _, _ = await _record(client)
    resp = await client.get(f"/api/table/{tid}/record/recZZZZZZZZZZZZZZZZ/status")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"isDeleted": True, "isVisible": False}


async def test_status_filtered_out(client):
    tid, rid, fid = await _record(client)
    flt = json.dumps(
        {
            "conjunction": "and",
            "filterSet": [{"fieldId": fid, "operator": "is", "value": "nomatch"}],
        }
    )
    resp = await client.get(f"/api/table/{tid}/record/{rid}/status", params={"filter": flt})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"isDeleted": False, "isVisible": False}
