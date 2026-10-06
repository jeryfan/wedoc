"""Aggregation record-index parity.

GET /aggregation/record-index?recordId=... returns {index} (0-based position in
the view's ordering); orderBy reorders it; a missing or filtered-out record
yields an empty 200 body.
"""

import json

from conftest import signup as _signup


async def _table(client):
    await _signup(client)
    sid = (await client.post("/api/space", json={"name": "S"})).json()["id"]
    bid = (await client.post("/api/base", json={"spaceId": sid, "name": "B"})).json()["id"]
    tid = (await client.post(f"/api/base/{bid}/table", json={"name": "T1"})).json()["id"]
    fld = (await client.get(f"/api/table/{tid}/field")).json()[0]
    ids = []
    for v in ("a", "b", "c"):
        ids.append(
            (
                await client.post(
                    f"/api/table/{tid}/record",
                    json={"records": [{"fields": {fld["name"]: v}}], "fieldKeyType": "name"},
                )
            ).json()["records"][0]["id"]
        )
    vid = (await client.get(f"/api/table/{tid}/view")).json()[0]["id"]
    return tid, vid, fld["id"], ids


async def test_record_index_default_and_desc(client):
    tid, vid, fid, ids = await _table(client)
    A = f"/api/table/{tid}/aggregation/record-index"
    first = await client.get(A, params={"recordId": ids[0], "viewId": vid})
    assert first.status_code == 200 and first.json() == {"index": 0}, first.text
    second = await client.get(A, params={"recordId": ids[1], "viewId": vid})
    assert second.json() == {"index": 1}
    ob = json.dumps([{"fieldId": fid, "order": "desc"}])
    desc = await client.get(A, params={"recordId": ids[0], "viewId": vid, "orderBy": ob})
    assert desc.json() == {"index": 2}


async def test_record_index_missing_and_filtered_empty(client):
    tid, vid, fid, ids = await _table(client)
    A = f"/api/table/{tid}/aggregation/record-index"
    missing = await client.get(A, params={"recordId": "recZZZZZZZZZZZZZZZZ", "viewId": vid})
    assert missing.status_code == 200 and missing.content == b"", missing.text
    flt = json.dumps(
        {"conjunction": "and", "filterSet": [{"fieldId": fid, "operator": "is", "value": "no"}]}
    )
    out = await client.get(A, params={"recordId": ids[0], "viewId": vid, "filter": flt})
    assert out.status_code == 200 and out.content == b"", out.text
