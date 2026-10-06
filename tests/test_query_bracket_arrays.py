"""Bracket-notation query-array parsing parity (qs semantics).

The web client serializes array query params as `key[]=a&key[]=b` (qs/axios).
These cover the shared `query_list` / `query_array` readers: bracketed arrays
must be honoured (not dropped), a single bracket value is a one-element array,
and strict-array params still reject a single plain scalar exactly like the
reference's zod validation.
"""

from conftest import signup as _signup


async def _create(client, path, body):
    resp = await client.post(path, json=body)
    assert resp.status_code in (200, 201), (path, resp.status_code, resp.text)
    return resp.json()


async def _table(client, names_counts):
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    base = await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"})
    table = await _create(client, f"/api/base/{base['id']}/table", {"name": "T1"})
    tid = table["id"]
    fields = {f["name"]: f["id"] for f in (await client.get(f"/api/table/{tid}/field")).json()}
    made = await _create(
        client,
        f"/api/table/{tid}/record",
        {
            "fieldKeyType": "name",
            "records": [{"fields": {"Name": n, "Count": c}} for n, c in names_counts],
        },
    )
    return tid, fields, [r["id"] for r in made["records"]]


async def test_record_list_projection_bracket_single(client):
    tid, fields, _ = await _table(client, [("a", 1), ("b", 2)])
    resp = await client.get(
        f"/api/table/{tid}/record?fieldKeyType=name&projection[]={fields['Name']}"
    )
    assert resp.status_code == 200, resp.text
    for rec in resp.json()["records"]:
        assert sorted(rec["fields"].keys()) == ["Name"]


async def test_record_list_projection_bracket_multi(client):
    tid, fields, _ = await _table(client, [("a", 1), ("b", 2)])
    url = (
        f"/api/table/{tid}/record?fieldKeyType=name"
        f"&projection[]={fields['Name']}&projection[]={fields['Count']}"
    )
    resp = await client.get(url)
    assert resp.status_code == 200, resp.text
    assert sorted(resp.json()["records"][0]["fields"].keys()) == ["Count", "Name"]


async def test_record_list_projection_plain_single_coerced(client):
    # record projection is z.union([z.string(), z.string().array()]): a lone
    # plain value is coerced to a one-element array (no 400).
    tid, fields, _ = await _table(client, [("a", 1)])
    resp = await client.get(
        f"/api/table/{tid}/record?fieldKeyType=name&projection={fields['Name']}"
    )
    assert resp.status_code == 200, resp.text
    assert sorted(resp.json()["records"][0]["fields"].keys()) == ["Name"]


async def test_record_get_projection_bracket_single(client):
    tid, fields, ids = await _table(client, [("a", 1)])
    resp = await client.get(
        f"/api/table/{tid}/record/{ids[0]}?fieldKeyType=name&projection[]={fields['Name']}"
    )
    assert resp.status_code == 200, resp.text
    assert sorted(resp.json()["fields"].keys()) == ["Name"]


async def test_agg_selected_record_ids_bracket_single(client):
    tid, _, ids = await _table(client, [("a", 1), ("b", 2), ("c", 3)])
    resp = await client.get(f"/api/table/{tid}/aggregation/row-count?selectedRecordIds[]={ids[0]}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["rowCount"] == 1


async def test_agg_selected_record_ids_bracket_multi(client):
    tid, _, ids = await _table(client, [("a", 1), ("b", 2), ("c", 3)])
    resp = await client.get(
        f"/api/table/{tid}/aggregation/row-count"
        f"?selectedRecordIds[]={ids[0]}&selectedRecordIds[]={ids[1]}"
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["rowCount"] == 2


async def test_agg_selected_record_ids_plain_multi(client):
    tid, _, ids = await _table(client, [("a", 1), ("b", 2), ("c", 3)])
    resp = await client.get(
        f"/api/table/{tid}/aggregation/row-count"
        f"?selectedRecordIds={ids[0]}&selectedRecordIds={ids[1]}"
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["rowCount"] == 2


async def test_agg_selected_record_ids_plain_single_400(client):
    tid, _, ids = await _table(client, [("a", 1), ("b", 2), ("c", 3)])
    resp = await client.get(f"/api/table/{tid}/aggregation/row-count?selectedRecordIds={ids[0]}")
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["message"] == (
        'Validation error: Invalid input: expected array, received string at "selectedRecordIds"'
    )
    assert body["code"] == "validation_error"


async def test_agg_projection_plain_single_400(client):
    # aggregation projection is a bare z.array(): a single plain value is rejected.
    tid, fields, _ = await _table(client, [("a", 1)])
    resp = await client.get(f"/api/table/{tid}/aggregation/row-count?projection={fields['Name']}")
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == (
        'Validation error: Invalid input: expected array, received string at "projection"'
    )


async def test_agg_search_count_bracket_single(client):
    # a one-element bracket search is a valid 1-tuple: searches every field.
    tid, _, _ = await _table(client, [("a", 1), ("b", 2), ("c", 3)])
    resp = await client.get(f"/api/table/{tid}/aggregation/search-count?search[]=a")
    assert resp.status_code == 200, resp.text
    assert resp.json()["count"] == 1


async def test_agg_search_count_plain_single_400(client):
    tid, _, _ = await _table(client, [("a", 1)])
    resp = await client.get(f"/api/table/{tid}/aggregation/search-count?search=a")
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == (
        'Validation error: Invalid input: expected tuple, received string at "search"'
    )


async def test_field_list_projection_bracket_single(client):
    tid, fields, _ = await _table(client, [("a", 1)])
    resp = await client.get(f"/api/table/{tid}/field?projection[]={fields['Name']}")
    assert resp.status_code == 200, resp.text
    assert [f["id"] for f in resp.json()] == [fields["Name"]]


async def test_field_list_projection_plain_single_400(client):
    tid, fields, _ = await _table(client, [("a", 1)])
    resp = await client.get(f"/api/table/{tid}/field?projection={fields['Name']}")
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == (
        'Validation error: Invalid input: expected array, received string at "projection"'
    )
