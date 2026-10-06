"""Aggregation search-index parity: matches + required-param errors.

search-index returns a list of {index, fieldId, recordId} for cells matching the
search term. take is required (missing -> 400 NaN); >1000 -> 400 max-result;
search is required (missing -> 400). No match yields an empty 200 body.
"""

from urllib.parse import urlencode

from conftest import signup as _signup


async def _table(client):
    await _signup(client)
    sid = (await client.post("/api/space", json={"name": "S"})).json()["id"]
    bid = (await client.post("/api/base", json={"spaceId": sid, "name": "B"})).json()["id"]
    tid = (await client.post(f"/api/base/{bid}/table", json={"name": "T1"})).json()["id"]
    nm = (await client.get(f"/api/table/{tid}/field")).json()[0]["name"]
    ids = set()
    for v in ("apple", "banana", "avocado"):
        ids.add(
            (
                await client.post(
                    f"/api/table/{tid}/record",
                    json={"records": [{"fields": {nm: v}}], "fieldKeyType": "name"},
                )
            ).json()["records"][0]["id"]
        )
    vid = (await client.get(f"/api/table/{tid}/view")).json()[0]["id"]
    return tid, vid, ids


async def test_search_index_matches(client):
    tid, vid, ids = await _table(client)
    q = urlencode([("search[]", "a"), ("take", "10"), ("viewId", vid)])
    resp = await client.get(f"/api/table/{tid}/aggregation/search-index?{q}")
    assert resp.status_code == 200, resp.text
    items = resp.json()
    assert len(items) == 3
    assert all(sorted(i.keys()) == ["fieldId", "index", "recordId"] for i in items)
    assert {i["recordId"] for i in items} == ids


async def test_search_index_no_match_empty(client):
    tid, vid, _ = await _table(client)
    q = urlencode([("search[]", "zzz"), ("take", "10"), ("viewId", vid)])
    resp = await client.get(f"/api/table/{tid}/aggregation/search-index?{q}")
    assert resp.status_code == 200 and resp.content == b"", resp.text


async def test_search_index_required_params(client):
    tid, vid, _ = await _table(client)
    base = f"/api/table/{tid}/aggregation/search-index"

    no_take = await client.get(f"{base}?{urlencode([('search[]', 'a'), ('viewId', vid)])}")
    assert no_take.status_code == 400
    assert no_take.json()["message"] == (
        'Validation error: Invalid input: expected number, received NaN at "take"'
    )

    no_search = await client.get(f"{base}?{urlencode([('take', '10'), ('viewId', vid)])}")
    assert no_search.status_code == 400
    assert no_search.json()["message"] == "Search query is required"

    over_q = urlencode([("search[]", "a"), ("take", "1001"), ("viewId", vid)])
    over = await client.get(f"{base}?{over_q}")
    assert over.status_code == 400
    assert over.json()["message"] == "The maximum search index result is 1000"
