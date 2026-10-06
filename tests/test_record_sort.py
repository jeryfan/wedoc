"""Record sort orders single-select by choice position, not alphabetically.

orderBy on a singleSelect field sorts by each cell's index in options.choices
(definition order), matching the reference; null cells sort first on asc / last
on desc.
"""

import json

from conftest import signup as _signup


async def _create(client, path, body):
    resp = await client.post(path, json=body)
    assert resp.status_code in (200, 201), (path, resp.status_code, resp.text)
    return resp.json()


async def _order_seq(client, tid, name_id, order_by):
    r = await client.get(
        f"/api/table/{tid}/record",
        params={"fieldKeyType": "id", "take": "50", "orderBy": json.dumps(order_by)},
    )
    assert r.status_code == 200, r.text
    return [x["fields"].get(name_id) or "∅" for x in r.json()["records"]]


async def test_single_select_sorts_by_choice_order(client):
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    base = await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"})
    table = await _create(client, f"/api/base/{base['id']}/table", {"name": "T1"})
    tid = table["id"]
    name_id = next(
        f["id"] for f in (await client.get(f"/api/table/{tid}/field")).json() if f.get("isPrimary")
    )
    # choices in a non-alphabetical definition order: Z, M, A
    sel = (
        await _create(
            client,
            f"/api/table/{tid}/field",
            {"name": "Sel", "type": "singleSelect",
             "options": {"choices": [{"name": "Z"}, {"name": "M"}, {"name": "A"}]}},
        )
    )["id"]
    for label, choice in (("a", "M"), ("b", "A"), ("e", "Z")):
        await _create(
            client,
            f"/api/table/{tid}/record",
            {
                "fieldKeyType": "id",
                "typecast": True,
                "records": [{"fields": {name_id: label, sel: choice}}],
            },
        )
    await _create(
        client,
        f"/api/table/{tid}/record",
        {"fieldKeyType": "id", "records": [{"fields": {name_id: "d"}}]},
    )
    # asc: null first, then by choice index Z(0)->M(1)->A(2) == e, a, b
    assert await _order_seq(client, tid, name_id, [{"fieldId": sel, "order": "asc"}]) == [
        "d",
        "e",
        "a",
        "b",
    ]
    # desc: reverse choice index, null last == b, a, e, d
    assert await _order_seq(client, tid, name_id, [{"fieldId": sel, "order": "desc"}]) == [
        "b",
        "a",
        "e",
        "d",
    ]


async def test_multi_select_sorts_by_choice_index_array(client):
    # multi-select sorts by the array of choice indices, compared
    # lexicographically: [Z] < [Z,M] < [M] < [M,Z] < [A]; null first on asc.
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    base = await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"})
    table = await _create(client, f"/api/base/{base['id']}/table", {"name": "T1"})
    tid = table["id"]
    name_id = next(
        f["id"] for f in (await client.get(f"/api/table/{tid}/field")).json() if f.get("isPrimary")
    )
    ms = (
        await _create(
            client,
            f"/api/table/{tid}/field",
            {"name": "MS", "type": "multipleSelect",
             "options": {"choices": [{"name": "Z"}, {"name": "M"}, {"name": "A"}]}},
        )
    )["id"]
    rows = [
        ("a", ["M"]),
        ("b", ["A"]),
        ("c", ["Z", "M"]),
        ("d", None),
        ("e", ["Z"]),
        ("f", ["M", "Z"]),
    ]
    for label, val in rows:
        fields = {name_id: label}
        if val is not None:
            fields[ms] = val
        await _create(
            client,
            f"/api/table/{tid}/record",
            {"fieldKeyType": "id", "typecast": True, "records": [{"fields": fields}]},
        )
    assert await _order_seq(client, tid, name_id, [{"fieldId": ms, "order": "asc"}]) == [
        "d",
        "e",
        "c",
        "a",
        "f",
        "b",
    ]
