"""conditionalRollup field parity: create validation, VO shape, computed cell.

A conditionalRollup runs a filtered query over a foreign table and rolls up one
of its fields — no link field, everything in `options`. Verified against the
reference oracle: filter is mandatory at create (400), the create VO mirrors
rollup (isComputed, derived cellValueType/dbFieldType, formatting default,
isPending only on create), and the cell resolves on record read from the
filtered foreign query.
"""

from conftest import signup as _signup


async def _create(client, path, body):
    resp = await client.post(path, json=body)
    assert resp.status_code in (200, 201), (path, resp.status_code, resp.text)
    return resp.json()


async def _setup(client):
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    base = await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"})
    t1 = await _create(client, f"/api/base/{base['id']}/table", {"name": "T1"})
    t2 = await _create(client, f"/api/base/{base['id']}/table", {"name": "T2"})
    t2_field_rows = (await client.get(f"/api/table/{t2['id']}/field")).json()
    t2_fields = {f["name"]: f["id"] for f in t2_field_rows}
    await _create(
        client,
        f"/api/table/{t2['id']}/record",
        {"fieldKeyType": "name", "records": [
            {"fields": {"Name": "x", "Count": 3}},
            {"fields": {"Name": "y", "Count": 10}},
            {"fields": {"Name": "z", "Count": 20}}]},
    )
    made = await _create(
        client,
        f"/api/table/{t1['id']}/record",
        {"fieldKeyType": "name", "records": [{"fields": {"Name": "r1"}}]},
    )
    return t1["id"], t2["id"], t2_fields["Count"], made["records"][0]["id"]


def _cr_body(name, expression, t2, count_id, value=10, operator="isGreaterEqual"):
    return {
        "type": "conditionalRollup",
        "name": name,
        "options": {
            "expression": expression,
            "foreignTableId": t2,
            "lookupFieldId": count_id,
            "filter": {
                "conjunction": "and",
                "filterSet": [{"fieldId": count_id, "operator": operator, "value": value}],
            },
        },
    }


async def test_conditional_rollup_filter_required(client):
    t1, t2, count_id, _rid = await _setup(client)
    resp = await client.post(
        f"/api/table/{t1}/field",
        json={"type": "conditionalRollup", "name": "CR", "options": {
            "expression": "sum({values})", "foreignTableId": t2, "lookupFieldId": count_id}},
    )
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["message"] == (
        'Validation error: Filter is required when type is conditionalRollup at "options"'
    )
    assert body["status"] == 400
    assert body["code"] == "validation_error"

    # a filter with no conditions counts as absent -> same 400.
    empty = await client.post(
        f"/api/table/{t1}/field",
        json={"type": "conditionalRollup", "name": "CR", "options": {
            "expression": "sum({values})", "foreignTableId": t2, "lookupFieldId": count_id,
            "filter": {"conjunction": "and", "filterSet": []}}},
    )
    assert empty.status_code == 400, empty.text
    assert empty.json()["message"] == (
        'Validation error: Filter is required when type is conditionalRollup at "options"'
    )


async def test_conditional_rollup_create_vo_matches_reference(client):
    t1, t2, count_id, _rid = await _setup(client)
    created = await _create(
        client, f"/api/table/{t1}/field", _cr_body("CRsum", "sum({values})", t2, count_id)
    )
    expected = {
        "name": "CRsum",
        "unique": False,
        "isComputed": True,
        "cellValueType": "number",
        "dbFieldType": "REAL",
        "type": "conditionalRollup",
        "options": {
            "expression": "sum({values})",
            "formatting": {"type": "decimal", "precision": 2},
            "foreignTableId": t2,
            "lookupFieldId": count_id,
            "filter": {
                "conjunction": "and",
                "filterSet": [{"fieldId": count_id, "operator": "isGreaterEqual", "value": 10}],
            },
        },
        "isPending": True,
    }
    stripped = {k: v for k, v in created.items() if k not in ("id", "dbFieldName")}
    assert stripped == expected, created
    assert "lookupOptions" not in created

    # GET drops isPending (the reference reports it only on the create response).
    got = (await client.get(f"/api/table/{t1}/field/{created['id']}")).json()
    got_stripped = {k: v for k, v in got.items() if k not in ("id", "dbFieldName")}
    assert got_stripped == {k: v for k, v in expected.items() if k != "isPending"}, got
    assert "isPending" not in got


async def test_conditional_rollup_computes_cell_on_read(client):
    t1, t2, count_id, rid = await _setup(client)
    fields = {
        "sum": await _create(client, f"/api/table/{t1}/field",
                              _cr_body("CRsum", "sum({values})", t2, count_id)),
        "count": await _create(client, f"/api/table/{t1}/field",
                               _cr_body("CRcount", "count({values})", t2, count_id)),
        "max": await _create(client, f"/api/table/{t1}/field",
                             _cr_body("CRmax", "max({values})", t2, count_id)),
        # no foreign row matches Count >= 999
        "empty": await _create(client, f"/api/table/{t1}/field",
                               _cr_body("CRempty", "sum({values})", t2, count_id, value=999)),
    }
    cells = (await client.get(f"/api/table/{t1}/record/{rid}?fieldKeyType=id")).json()["fields"]
    # Count values 10 and 20 match Count >= 10.
    assert cells[fields["sum"]["id"]] == 30
    assert cells[fields["count"]["id"]] == 2
    assert cells[fields["max"]["id"]] == 20
    # empty match: sum degrades to 0 (matching the reference's empty rollup value).
    assert cells[fields["empty"]["id"]] == 0

