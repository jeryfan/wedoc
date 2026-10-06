"""search-index returns one hit per field that actually matches on a row (not
one per searchable field), ordered by row position then field order.
"""

from conftest import signup as _signup


async def _create(client, path, body):
    resp = await client.post(path, json=body)
    assert resp.status_code in (200, 201), (path, resp.status_code, resp.text)
    return resp.json()


async def _setup(client):
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    bid = (await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"}))["id"]
    ta = (await _create(client, f"/api/base/{bid}/table", {"name": "TA"}))["id"]
    name = next(f["id"] for f in (await client.get(f"/api/table/{ta}/field")).json()
                if f["name"] == "Name")
    extra = (await _create(client, f"/api/table/{ta}/field",
        {"name": "Extra", "type": "singleLineText"}))["id"]
    for nm, ev in [("cat", "cat"), ("cat", "dog"), ("fish", "cat")]:
        await _create(client, f"/api/table/{ta}/record",
            {"fieldKeyType": "name", "records": [{"fields": {"Name": nm, "Extra": ev}}]})
    return ta, name, extra


async def test_search_index_matches_only_matching_fields(client):
    ta, name, extra = await _setup(client)
    resp = await client.get(f"/api/table/{ta}/aggregation/search-index?search[]=cat&take=100")
    assert resp.status_code == 200, resp.text
    seq = [(m["index"], m["fieldId"]) for m in resp.json()]
    # row1 (cat/cat) matches both fields; row2 (cat/dog) only Name; row3 (fish/cat) only Extra.
    assert seq == [(1, name), (1, extra), (2, name), (3, extra)]
