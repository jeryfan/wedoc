"""Group-points ordering parity for a singleSelect groupBy.

Groups sort by choice definition order with PostgreSQL default null placement:
asc -> choices then null (NULLS LAST); desc -> null then reversed choices
(NULLS FIRST). Counts sit in the type:1 rows following each header.
"""

import json

from conftest import signup as _signup


async def _table_with_groups(client):
    await _signup(client)
    sid = (await client.post("/api/space", json={"name": "S"})).json()["id"]
    bid = (await client.post("/api/base", json={"spaceId": sid, "name": "B"})).json()["id"]
    tid = (await client.post(f"/api/base/{bid}/table", json={"name": "T1"})).json()["id"]
    flds = (await client.get(f"/api/table/{tid}/field")).json()
    status = next(f for f in flds if f["type"] == "singleSelect")
    choices = [ch["name"] for ch in status["options"]["choices"]]
    for v in (choices[0], choices[0], choices[1], None):
        await client.post(
            f"/api/table/{tid}/record",
            json={"records": [{"fields": {status["name"]: v}}], "fieldKeyType": "name"},
        )
    vid = (await client.get(f"/api/table/{tid}/view")).json()[0]["id"]
    return tid, vid, status["id"], choices


def _headers(points):
    return [(p["value"], nxt["count"]) for p, nxt in zip(points[::2], points[1::2], strict=False)]


async def test_group_points_asc_nulls_last(client):
    tid, vid, fid, choices = await _table_with_groups(client)
    gb = json.dumps([{"fieldId": fid, "order": "asc"}])
    resp = await client.get(
        f"/api/table/{tid}/aggregation/group-points", params={"groupBy": gb, "viewId": vid}
    )
    assert resp.status_code == 200, resp.text
    assert _headers(resp.json()) == [(choices[0], 2), (choices[1], 1), (None, 1)]


async def test_group_points_desc_nulls_first(client):
    tid, vid, fid, choices = await _table_with_groups(client)
    gb = json.dumps([{"fieldId": fid, "order": "desc"}])
    resp = await client.get(
        f"/api/table/{tid}/aggregation/group-points", params={"groupBy": gb, "viewId": vid}
    )
    assert resp.status_code == 200, resp.text
    assert _headers(resp.json()) == [(None, 1), (choices[1], 1), (choices[0], 2)]
