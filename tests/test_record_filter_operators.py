"""Filter operators for single-select and checkbox match the reference SQL.

Single-select supports isAnyOf ($in) and isNoneOf ($notIn, keeping null/empty
cells); checkbox `is` matches `= true` for truthy and `(= false OR null)` for
falsy so unchecked cells count as false.
"""

import json

from conftest import signup as _signup


async def _create(client, path, body):
    resp = await client.post(path, json=body)
    assert resp.status_code in (200, 201), (path, resp.status_code, resp.text)
    return resp.json()


async def _setup(client):
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    base = await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"})
    table = await _create(client, f"/api/base/{base['id']}/table", {"name": "T1"})
    tid = table["id"]
    name_id = next(
        f["id"] for f in (await client.get(f"/api/table/{tid}/field")).json() if f.get("isPrimary")
    )
    sel = (
        await _create(
            client,
            f"/api/table/{tid}/field",
            {"name": "Sel", "type": "singleSelect",
             "options": {"choices": [{"name": "A"}, {"name": "B"}, {"name": "C"}]}},
        )
    )["id"]
    chk = (
        await _create(client, f"/api/table/{tid}/field", {"name": "Chk", "type": "checkbox"})
    )["id"]
    rows = [
        {name_id: "apple", sel: "A", chk: True},
        {name_id: "banana", sel: "B", chk: False},
        {name_id: "apricot", sel: "A"},
        {name_id: "empty"},
    ]
    for r in rows:
        await _create(
            client,
            f"/api/table/{tid}/record",
            {"fieldKeyType": "id", "records": [{"fields": r}]},
        )
    return tid, name_id, sel, chk


async def _filter_names(client, tid, name_id, fid, op, val):
    fltr = {"conjunction": "and", "filterSet": [{"fieldId": fid, "operator": op, "value": val}]}
    r = await client.get(
        f"/api/table/{tid}/record",
        params={"fieldKeyType": "id", "take": "100", "filter": json.dumps(fltr)},
    )
    assert r.status_code == 200, (op, r.status_code, r.text)
    return sorted((rec["fields"].get(name_id) or "∅") for rec in r.json()["records"])


async def test_single_select_is_any_of(client):
    tid, name_id, sel, _chk = await _setup(client)
    assert await _filter_names(client, tid, name_id, sel, "isAnyOf", ["A", "B"]) == [
        "apple",
        "apricot",
        "banana",
    ]


async def test_single_select_is_none_of_keeps_empty(client):
    tid, name_id, sel, _chk = await _setup(client)
    # isNoneOf ["A"] excludes the A rows but keeps B and the empty (null) cell.
    assert await _filter_names(client, tid, name_id, sel, "isNoneOf", ["A"]) == ["banana", "empty"]


async def test_checkbox_is_true(client):
    tid, name_id, _sel, chk = await _setup(client)
    assert await _filter_names(client, tid, name_id, chk, "is", True) == ["apple"]


async def test_checkbox_is_false_matches_unchecked(client):
    tid, name_id, _sel, chk = await _setup(client)
    # false OR null: the explicit false and the two never-set cells.
    assert await _filter_names(client, tid, name_id, chk, "is", False) == [
        "apricot",
        "banana",
        "empty",
    ]


async def _setup_user(client):
    await _signup(client)
    uid = (await client.get("/api/auth/user")).json()["id"]
    space = await _create(client, "/api/space", {"name": "S"})
    base = await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"})
    table = await _create(client, f"/api/base/{base['id']}/table", {"name": "T1"})
    tid = table["id"]
    name_id = next(
        f["id"] for f in (await client.get(f"/api/table/{tid}/field")).json() if f.get("isPrimary")
    )
    usr = (await _create(client, f"/api/table/{tid}/field", {"name": "U", "type": "user"}))["id"]
    await _create(
        client,
        f"/api/table/{tid}/record",
        {
            "fieldKeyType": "id",
            "typecast": True,
            "records": [{"fields": {name_id: "r1", usr: {"id": uid}}}],
        },
    )
    await _create(
        client,
        f"/api/table/{tid}/record",
        {"fieldKeyType": "id", "records": [{"fields": {name_id: "r2"}}]},
    )
    return tid, name_id, usr, uid


async def test_user_is_and_is_not(client):
    tid, name_id, usr, uid = await _setup_user(client)
    assert await _filter_names(client, tid, name_id, usr, "is", uid) == ["r1"]
    assert await _filter_names(client, tid, name_id, usr, "isNot", uid) == ["r2"]


async def test_user_is_any_of_and_none_of(client):
    tid, name_id, usr, uid = await _setup_user(client)
    assert await _filter_names(client, tid, name_id, usr, "isAnyOf", [uid]) == ["r1"]
    # isNoneOf keeps the empty (null) cell.
    assert await _filter_names(client, tid, name_id, usr, "isNoneOf", [uid]) == ["r2"]


async def _setup_multi_user(client):
    await _signup(client)
    uid = (await client.get("/api/auth/user")).json()["id"]
    space = await _create(client, "/api/space", {"name": "S"})
    base = await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"})
    table = await _create(client, f"/api/base/{base['id']}/table", {"name": "T1"})
    tid = table["id"]
    name_id = next(
        f["id"] for f in (await client.get(f"/api/table/{tid}/field")).json() if f.get("isPrimary")
    )
    usr = (
        await _create(
            client,
            f"/api/table/{tid}/field",
            {"name": "U", "type": "user", "options": {"isMultiple": True}},
        )
    )["id"]
    await _create(
        client,
        f"/api/table/{tid}/record",
        {
            "fieldKeyType": "id",
            "typecast": True,
            "records": [{"fields": {name_id: "r1", usr: [{"id": uid}]}}],
        },
    )
    await _create(
        client,
        f"/api/table/{tid}/record",
        {"fieldKeyType": "id", "records": [{"fields": {name_id: "r2"}}]},
    )
    return tid, name_id, usr, uid


async def test_multi_user_has_and_exactly_and_empty(client):
    tid, name_id, usr, uid = await _setup_multi_user(client)
    assert await _filter_names(client, tid, name_id, usr, "hasAnyOf", [uid]) == ["r1"]
    assert await _filter_names(client, tid, name_id, usr, "hasAllOf", [uid]) == ["r1"]
    assert await _filter_names(client, tid, name_id, usr, "hasNoneOf", [uid]) == ["r2"]
    assert await _filter_names(client, tid, name_id, usr, "isExactly", [uid]) == ["r1"]
    assert await _filter_names(client, tid, name_id, usr, "isNotExactly", [uid]) == ["r2"]
    assert await _filter_names(client, tid, name_id, usr, "isEmpty", None) == ["r2"]
    assert await _filter_names(client, tid, name_id, usr, "isNotEmpty", None) == ["r1"]


async def _setup_system_user(client, field_type):
    await _signup(client)
    uid = (await client.get("/api/auth/user")).json()["id"]
    space = await _create(client, "/api/space", {"name": "S"})
    base = await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"})
    table = await _create(client, f"/api/base/{base['id']}/table", {"name": "T1"})
    tid = table["id"]
    name_id = next(
        f["id"] for f in (await client.get(f"/api/table/{tid}/field")).json() if f.get("isPrimary")
    )
    field = await _create(
        client, f"/api/table/{tid}/field", {"name": "SU", "type": field_type}
    )
    fid = field["id"]
    for label in ("r1", "r2"):
        await _create(
            client,
            f"/api/table/{tid}/record",
            {"fieldKeyType": "id", "records": [{"fields": {name_id: label}}]},
        )
    return tid, name_id, fid, uid


async def test_created_by_filter_matches_creator(client):
    tid, name_id, fid, uid = await _setup_system_user(client, "createdBy")
    assert await _filter_names(client, tid, name_id, fid, "is", uid) == ["r1", "r2"]
    assert await _filter_names(client, tid, name_id, fid, "isNot", uid) == []
    assert await _filter_names(client, tid, name_id, fid, "isAnyOf", [uid]) == ["r1", "r2"]
    assert await _filter_names(client, tid, name_id, fid, "isNoneOf", [uid]) == []
    assert await _filter_names(client, tid, name_id, fid, "isEmpty", None) == []
    assert await _filter_names(client, tid, name_id, fid, "isNotEmpty", None) == ["r1", "r2"]


async def test_last_modified_by_filter(client):
    tid, name_id, fid, uid = await _setup_system_user(client, "lastModifiedBy")
    assert await _filter_names(client, tid, name_id, fid, "isAnyOf", [uid]) == ["r1", "r2"]
    assert await _filter_names(client, tid, name_id, fid, "isNoneOf", [uid]) == []


async def _setup_system_time(client, field_type):
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    base = await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"})
    table = await _create(client, f"/api/base/{base['id']}/table", {"name": "T1"})
    tid = table["id"]
    name_id = next(
        f["id"] for f in (await client.get(f"/api/table/{tid}/field")).json() if f.get("isPrimary")
    )
    field = await _create(
        client, f"/api/table/{tid}/field", {"name": "ST", "type": field_type}
    )
    fid = field["id"]
    for label in ("r1", "r2"):
        await _create(
            client,
            f"/api/table/{tid}/record",
            {"fieldKeyType": "id", "records": [{"fields": {name_id: label}}]},
        )
    return tid, name_id, fid


async def test_created_time_filter_targets_system_column(client):
    tid, name_id, fid = await _setup_system_time(client, "createdTime")
    tz = {"mode": "today", "timeZone": "UTC"}
    assert await _filter_names(client, tid, name_id, fid, "isEmpty", None) == []
    assert await _filter_names(client, tid, name_id, fid, "isNotEmpty", None) == ["r1", "r2"]
    assert await _filter_names(client, tid, name_id, fid, "isWithIn", tz) == ["r1", "r2"]
    assert await _filter_names(client, tid, name_id, fid, "isOnOrAfter", tz) == ["r1", "r2"]


async def _num_and_checkbox(client):
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    base = await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"})
    table = await _create(client, f"/api/base/{base['id']}/table", {"name": "T1"})
    tid = table["id"]
    num = (
        await _create(client, f"/api/table/{tid}/field", {"name": "Num", "type": "number"})
    )["id"]
    chk = (
        await _create(client, f"/api/table/{tid}/field", {"name": "Chk", "type": "checkbox"})
    )["id"]
    return tid, num, chk


async def _expect_invalid_operator(client, tid, fid, op, val):
    fltr = {"conjunction": "and", "filterSet": [{"fieldId": fid, "operator": op, "value": val}]}
    r = await client.get(
        f"/api/table/{tid}/record",
        params={"fieldKeyType": "id", "take": "10", "filter": json.dumps(fltr)},
    )
    assert r.status_code == 400, (op, r.status_code, r.text)
    assert r.json()["message"] == "Invalid record condition operator for field"


async def test_operator_not_allowed_for_field_type_is_400(client):
    # getValidFilterOperators: operators outside a field type's allow-list are a
    # 400 (never a 500) -- e.g. contains on number, isNot/isNotEmpty on checkbox.
    tid, num, chk = await _num_and_checkbox(client)
    await _expect_invalid_operator(client, tid, num, "contains", "1")
    await _expect_invalid_operator(client, tid, num, "doesNotContain", "1")
    await _expect_invalid_operator(client, tid, num, "hasAnyOf", [1])
    await _expect_invalid_operator(client, tid, chk, "isNot", True)
    await _expect_invalid_operator(client, tid, chk, "isNotEmpty", None)
