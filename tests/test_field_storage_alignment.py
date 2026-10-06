"""Physical-storage + dbFieldType alignment for multipleSelect and user fields.

The reference stores a multipleSelect cell as JSON (jsonb: an array of choice
names) with dbFieldType "JSON", and a user cell's dbFieldType/physical column
depends on options.isMultiple: single -> TEXT (a text column holding one JSON
object), multi -> JSON (jsonb). Verifies the VO labels, the cell round-trip
through the physical column, and the multipleSelect membership filters.
"""

from conftest import signup as _signup


async def _create(client, path, body):
    resp = await client.post(path, json=body)
    assert resp.status_code in (200, 201), (path, resp.status_code, resp.text)
    return resp.json()


async def _setup(client):
    await _signup(client)
    me = (await client.get("/api/auth/user")).json()
    space = await _create(client, "/api/space", {"name": "S"})
    base = await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"})
    table = await _create(client, f"/api/base/{base['id']}/table", {"name": "T1"})
    tid = table["id"]
    fields = (await client.get(f"/api/table/{tid}/field")).json()
    primary = next(f for f in fields if f.get("isPrimary"))
    view = (await client.get(f"/api/table/{tid}/view")).json()[0]
    return tid, primary["id"], view["id"], me


async def test_db_field_type_alignment(client):
    tid, _primary, _vid, _me = await _setup(client)
    ms = await _create(
        client,
        f"/api/table/{tid}/field",
        {"type": "multipleSelect", "name": "MSel",
         "options": {"choices": [{"name": "a"}, {"name": "b"}, {"name": "c"}]}},
    )
    assert ms["dbFieldType"] == "JSON"
    assert ms["cellValueType"] == "string"
    assert ms["isMultipleCellValue"] is True

    single = await _create(
        client, f"/api/table/{tid}/field",
        {"type": "user", "name": "USingle", "options": {"isMultiple": False}},
    )
    assert single["dbFieldType"] == "TEXT"
    assert single["cellValueType"] == "string"
    assert "isMultipleCellValue" not in single

    multi = await _create(
        client, f"/api/table/{tid}/field",
        {"type": "user", "name": "UMulti", "options": {"isMultiple": True}},
    )
    assert multi["dbFieldType"] == "JSON"
    assert multi["isMultipleCellValue"] is True

    ss = await _create(
        client, f"/api/table/{tid}/field",
        {"type": "singleSelect", "name": "SSel", "options": {"choices": [{"name": "a"}]}},
    )
    assert ss["dbFieldType"] == "TEXT"
    assert "isMultipleCellValue" not in ss

    # the labels survive a re-read.
    for created in (ms, single, multi, ss):
        got = (await client.get(f"/api/table/{tid}/field/{created['id']}")).json()
        assert got["dbFieldType"] == created["dbFieldType"], got
        assert got.get("isMultipleCellValue") == created.get("isMultipleCellValue")


async def test_multiselect_and_user_cell_roundtrip(client):
    tid, primary, _vid, me = await _setup(client)
    uid = me["id"]
    ms = await _create(
        client, f"/api/table/{tid}/field",
        {"type": "multipleSelect", "name": "MSel",
         "options": {"choices": [{"name": "a"}, {"name": "b"}, {"name": "c"}]}},
    )
    single = await _create(
        client, f"/api/table/{tid}/field",
        {"type": "user", "name": "USingle", "options": {"isMultiple": False}},
    )
    multi = await _create(
        client, f"/api/table/{tid}/field",
        {"type": "user", "name": "UMulti", "options": {"isMultiple": True}},
    )
    made = await _create(
        client, f"/api/table/{tid}/record",
        {"fieldKeyType": "id", "records": [{"fields": {
            primary: "r1",
            ms["id"]: ["a", "b"],
            single["id"]: {"id": uid, "title": me["name"]},
            multi["id"]: [{"id": uid, "title": me["name"]}],
        }}]},
    )
    rid = made["records"][0]["id"]
    cells = (await client.get(f"/api/table/{tid}/record/{rid}?fieldKeyType=id")).json()["fields"]
    # multiSelect: jsonb array of choice-name strings, preserved verbatim & ordered.
    assert cells[ms["id"]] == ["a", "b"]
    # single user: text column holding one JSON object, round-tripped intact.
    assert cells[single["id"]]["id"] == uid
    assert cells[single["id"]]["title"] == me["name"]
    # multi user: jsonb array of one object.
    assert isinstance(cells[multi["id"]], list)
    assert cells[multi["id"]][0]["id"] == uid

    # a PATCH rewriting the multiSelect cell still round-trips.
    await client.patch(
        f"/api/table/{tid}/record/{rid}",
        json={"fieldKeyType": "id", "record": {"fields": {ms["id"]: ["c"]}}},
    )
    cells = (await client.get(f"/api/table/{tid}/record/{rid}?fieldKeyType=id")).json()["fields"]
    assert cells[ms["id"]] == ["c"]


async def _rows(client, tid, vid, ms_id, op, value):
    import json as _json
    cond = {"fieldId": ms_id, "operator": op, "isSymbol": False}
    if value is not None:
        cond["value"] = value
    fj = _json.dumps({"conjunction": "and", "filterSet": [cond]})
    resp = await client.get(
        f"/api/table/{tid}/record",
        params={"fieldKeyType": "id", "viewId": vid, "filter": fj},
    )
    assert resp.status_code == 200, resp.text
    out = []
    for rec in resp.json()["records"]:
        vals = list(rec["fields"].values())
        # primary is the first non-null string cell "rN"
        out.append(next((v for v in vals if isinstance(v, str) and v.startswith("r")), None))
    return sorted(v for v in out if v)


async def test_multiselect_membership_filters(client):
    tid, primary, vid, _me = await _setup(client)
    ms = await _create(
        client, f"/api/table/{tid}/field",
        {"type": "multipleSelect", "name": "MSel",
         "options": {"choices": [{"name": "a"}, {"name": "b"}, {"name": "c"}]}},
    )
    msid = ms["id"]
    await _create(
        client, f"/api/table/{tid}/record",
        {"fieldKeyType": "id", "records": [
            {"fields": {primary: "r1", msid: ["a", "b"]}},
            {"fields": {primary: "r2", msid: ["b", "c"]}},
            {"fields": {primary: "r3", msid: ["c"]}},
            {"fields": {primary: "r4"}},
        ]},
    )
    assert await _rows(client, tid, vid, msid, "hasAnyOf", ["a"]) == ["r1"]
    assert await _rows(client, tid, vid, msid, "hasAnyOf", ["b", "c"]) == ["r1", "r2", "r3"]
    assert await _rows(client, tid, vid, msid, "hasAllOf", ["b", "c"]) == ["r2"]
    assert await _rows(client, tid, vid, msid, "hasNoneOf", ["b", "c"]) == ["r4"]
    assert await _rows(client, tid, vid, msid, "isExactly", ["a", "b"]) == ["r1"]
    assert await _rows(client, tid, vid, msid, "isNotExactly", ["b", "c"]) == ["r1", "r3", "r4"]
    assert await _rows(client, tid, vid, msid, "isEmpty", None) == ["r4"]
    assert await _rows(client, tid, vid, msid, "isNotEmpty", None) == ["r1", "r2", "r3"]


async def test_multiselect_rejects_unsupported_operator(client):
    tid, _primary, vid, _me = await _setup(client)
    ms = await _create(
        client, f"/api/table/{tid}/field",
        {"type": "multipleSelect", "name": "MSel",
         "options": {"choices": [{"name": "a"}]}},
    )
    import json as _json
    fj = _json.dumps({"conjunction": "and", "filterSet": [
        {"fieldId": ms["id"], "operator": "isAnyOf", "value": ["a"], "isSymbol": False}]})
    resp = await client.get(
        f"/api/table/{tid}/record",
        params={"fieldKeyType": "id", "viewId": vid, "filter": fj},
    )
    assert resp.status_code == 400, resp.text


async def test_user_cell_enriched_on_read(client):
    # the reference re-resolves user cells to {id,title,email,avatarUrl} on read;
    # wedoc persists {id,title} and enriches at read time.
    tid, _primary, _view, me = await _setup(client)
    uf = await _create(
        client,
        f"/api/table/{tid}/field",
        {"type": "user", "name": "U", "options": {"isMultiple": False}},
    )
    made = await _create(
        client,
        f"/api/table/{tid}/record",
        {"fieldKeyType": "id", "records": [{"fields": {uf["id"]: {"id": me["id"], "title": "x"}}}]},
    )
    rid = made["records"][0]["id"]
    got = (await client.get(f"/api/table/{tid}/record/{rid}?fieldKeyType=id")).json()
    cell = got["fields"][uf["id"]]
    assert cell["id"] == me["id"]
    assert "email" in cell
    assert cell["title"] == me["name"]  # refreshed from the user record, not the written title
