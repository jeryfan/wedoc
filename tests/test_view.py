"""View sort/group body validation parity.

Ports viewSortSchema / viewGroupSchema: each item is { fieldId: string, order:
"asc"|"desc" } (extra keys tolerated, a null value clears the prop). Invalid
items report zod-pathed errors at sort.sortObjs[i].* / group[i].*.
"""

from conftest import signup as _signup

_ORDER_MSG = 'Invalid option: expected one of "asc"|"desc"'


async def _view(client):
    await _signup(client)
    sid = (await client.post("/api/space", json={"name": "S"})).json()["id"]
    bid = (await client.post("/api/base", json={"spaceId": sid, "name": "A"})).json()["id"]
    tid = (await client.post(f"/api/base/{bid}/table", json={"name": "TA"})).json()["id"]
    vid = (await client.get(f"/api/table/{tid}/view")).json()[0]["id"]
    fid = (await client.get(f"/api/table/{tid}/field")).json()[0]["id"]
    return tid, vid, fid


async def test_sort_bad_order(client):
    tid, vid, fid = await _view(client)
    resp = await client.put(
        f"/api/table/{tid}/view/{vid}/sort",
        json={"sort": {"sortObjs": [{"fieldId": fid, "order": "x"}]}},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == f'Validation error: {_ORDER_MSG} at "sort.sortObjs[0].order"'


async def test_sort_missing_field_id(client):
    tid, vid, _fid = await _view(client)
    resp = await client.put(
        f"/api/table/{tid}/view/{vid}/sort", json={"sort": {"sortObjs": [{"order": "asc"}]}}
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == (
        'Validation error: Invalid input: expected string, received undefined '
        'at "sort.sortObjs[0].fieldId"'
    )


async def test_sort_missing_order_is_option_error(client):
    tid, vid, fid = await _view(client)
    resp = await client.put(
        f"/api/table/{tid}/view/{vid}/sort", json={"sort": {"sortObjs": [{"fieldId": fid}]}}
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == f'Validation error: {_ORDER_MSG} at "sort.sortObjs[0].order"'


async def test_sort_objs_not_array(client):
    tid, vid, _fid = await _view(client)
    resp = await client.put(
        f"/api/table/{tid}/view/{vid}/sort", json={"sort": {"sortObjs": "x"}}
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == (
        'Validation error: Invalid input: expected array, received string at "sort.sortObjs"'
    )


async def test_sort_objs_absent(client):
    tid, vid, _fid = await _view(client)
    resp = await client.put(f"/api/table/{tid}/view/{vid}/sort", json={"sort": {}})
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == (
        'Validation error: Invalid input: expected array, received undefined at "sort.sortObjs"'
    )


async def test_sort_valid_and_null(client):
    tid, vid, fid = await _view(client)
    ok = await client.put(
        f"/api/table/{tid}/view/{vid}/sort",
        json={"sort": {"sortObjs": [{"fieldId": fid, "order": "desc"}], "zzz": 1}},
    )
    assert ok.status_code == 200, ok.text
    cleared = await client.put(f"/api/table/{tid}/view/{vid}/sort", json={"sort": None})
    assert cleared.status_code == 200, cleared.text


async def test_group_bad_order(client):
    tid, vid, fid = await _view(client)
    resp = await client.put(
        f"/api/table/{tid}/view/{vid}/group", json={"group": [{"fieldId": fid, "order": "x"}]}
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == f'Validation error: {_ORDER_MSG} at "group[0].order"'


async def test_group_missing_field_id(client):
    tid, vid, _fid = await _view(client)
    resp = await client.put(
        f"/api/table/{tid}/view/{vid}/group", json={"group": [{"order": "asc"}]}
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == (
        'Validation error: Invalid input: expected string, received undefined '
        'at "group[0].fieldId"'
    )


async def test_group_valid_and_null(client):
    tid, vid, fid = await _view(client)
    ok = await client.put(
        f"/api/table/{tid}/view/{vid}/group", json={"group": [{"fieldId": fid, "order": "asc"}]}
    )
    assert ok.status_code == 200, ok.text
    cleared = await client.put(f"/api/table/{tid}/view/{vid}/group", json={"group": None})
    assert cleared.status_code == 200, cleared.text


async def test_create_view_bad_sort(client):
    tid, _vid, fid = await _view(client)
    resp = await client.post(
        f"/api/table/{tid}/view",
        json={"type": "grid", "sort": {"sortObjs": [{"fieldId": fid, "order": "x"}]}},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == f'Validation error: {_ORDER_MSG} at "sort.sortObjs[0].order"'


async def test_create_view_bad_group(client):
    tid, _vid, fid = await _view(client)
    resp = await client.post(
        f"/api/table/{tid}/view",
        json={"type": "grid", "group": [{"fieldId": fid, "order": "x"}]},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == f'Validation error: {_ORDER_MSG} at "group[0].order"'
