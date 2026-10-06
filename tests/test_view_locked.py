"""View locked update parity: isLocked is an optional STRICT boolean.

PUT /view/{viewId}/locked accepts a boolean (200) or an absent isLocked (200,
no-op), but rejects a present null/string/number with
"Invalid input: expected boolean, received <type>".
"""

from conftest import signup as _signup


async def _view(client):
    await _signup(client)
    sid = (await client.post("/api/space", json={"name": "S"})).json()["id"]
    bid = (await client.post("/api/base", json={"spaceId": sid, "name": "B"})).json()["id"]
    tid = (await client.post(f"/api/base/{bid}/table", json={"name": "T1"})).json()["id"]
    vid = (await client.get(f"/api/table/{tid}/view")).json()[0]["id"]
    return tid, vid


async def test_locked_valid_and_missing(client):
    tid, vid = await _view(client)
    base = f"/api/table/{tid}/view/{vid}/locked"
    assert (await client.put(base, json={"isLocked": True})).status_code == 200
    got = await client.get(f"/api/table/{tid}/view/{vid}")
    assert got.json()["isLocked"] is True
    assert (await client.put(base, json={})).status_code == 200


async def test_locked_rejects_non_boolean(client):
    tid, vid = await _view(client)
    base = f"/api/table/{tid}/view/{vid}/locked"
    for value, received in ((None, "null"), ("yes", "string"), (1, "number")):
        resp = await client.put(base, json={"isLocked": value})
        assert resp.status_code == 400, resp.text
        assert resp.json()["message"] == (
            f'Validation error: Invalid input: expected boolean, received {received} at "isLocked"'
        )
