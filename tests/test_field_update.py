"""Field PATCH returns an empty 200 (reference parity); convert returns the VO.

The reference's PATCH /field/{id} (name/description/dbFieldName) responds with
an empty 200 body, while PUT /field/{id}/convert returns the resulting field
VO. Persistence is verified via a follow-up GET.
"""

from conftest import signup as _signup


async def _create(client, path, body):
    resp = await client.post(path, json=body)
    assert resp.status_code in (200, 201), (path, resp.status_code, resp.text)
    return resp.json()


async def _setup_field(client):
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    base = await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"})
    table = await _create(client, f"/api/base/{base['id']}/table", {"name": "T1"})
    field = await _create(
        client, f"/api/table/{table['id']}/field", {"type": "singleLineText", "name": "F"}
    )
    return table["id"], field["id"]


async def test_patch_field_returns_empty_200_and_persists(client):
    tid, fid = await _setup_field(client)

    resp = await client.patch(f"/api/table/{tid}/field/{fid}", json={"name": "Renamed"})
    assert resp.status_code == 200, resp.text
    assert resp.text == ""

    resp2 = await client.patch(f"/api/table/{tid}/field/{fid}", json={"description": "hello"})
    assert resp2.status_code == 200
    assert resp2.text == ""

    got = (await client.get(f"/api/table/{tid}/field/{fid}")).json()
    assert got["name"] == "Renamed"
    assert got["description"] == "hello"


async def test_convert_field_returns_vo(client):
    tid, fid = await _setup_field(client)

    resp = await client.put(
        f"/api/table/{tid}/field/{fid}/convert", json={"type": "number", "name": "F"}
    )
    assert resp.status_code == 200, resp.text
    vo = resp.json()
    assert vo["type"] == "number"
    assert vo["cellValueType"] == "number"


async def test_number_to_select_uses_raw_numeric_string(client):
    # number -> singleSelect stringifies the raw number (full precision, no
    # decimal formatting), unlike number -> text which stays formatted.
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    base = await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"})
    table = await _create(client, f"/api/base/{base['id']}/table", {"name": "T1"})
    tid = table["id"]
    name_id = next(
        f["id"] for f in (await client.get(f"/api/table/{tid}/field")).json() if f.get("isPrimary")
    )
    num = await _create(
        client,
        f"/api/table/{tid}/field",
        {
            "name": "N",
            "type": "number",
            "options": {"formatting": {"type": "decimal", "precision": 2}},
        },
    )
    fid = num["id"]
    for i, v in enumerate((10, 20.5, 3.14159)):
        await _create(
            client,
            f"/api/table/{tid}/record",
            {"fieldKeyType": "id", "records": [{"fields": {name_id: f"r{i}", fid: v}}]},
        )
    vo = (
        await client.put(
            f"/api/table/{tid}/field/{fid}/convert",
            json={"name": "N", "type": "singleSelect"},
        )
    ).json()
    choices = sorted(ch["name"] for ch in vo["options"]["choices"])
    assert choices == ["10", "20.5", "3.14159"]


async def _cell(client, tid, rid, fid):
    resp = await client.get(f"/api/table/{tid}/record/{rid}?fieldKeyType=id")
    assert resp.status_code == 200, resp.text
    return resp.json().get("fields", {}).get(fid)


async def _text_field_with(client, values):
    """A singleLineText field carrying one record per value; returns
    (table_id, field_id, [record_id...])."""
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    base = await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"})
    tid = (await _create(client, f"/api/base/{base['id']}/table", {"name": "T1"}))["id"]
    fid = (await _create(
        client, f"/api/table/{tid}/field", {"type": "singleLineText", "name": "F"}
    ))["id"]
    rids = []
    for v in values:
        rec = await _create(
            client, f"/api/table/{tid}/record",
            {"fieldKeyType": "id", "records": [{"fields": {fid: v}}]},
        )
        rids.append(rec["records"][0]["id"])
    return tid, fid, rids


async def test_empty_text_cell_stored_as_null(client):
    # the reference persists an empty text cell as NULL (empty string -> null).
    tid, fid, rids = await _text_field_with(client, ["", "keep"])
    assert await _cell(client, tid, rids[0], fid) is None
    assert await _cell(client, tid, rids[1], fid) == "keep"


async def test_convert_text_to_checkbox_nonempty_is_true(client):
    # field-converting checkbox uses convertStringToCellValue: any non-empty
    # string (incl "false") -> true; empty -> null.
    tid, fid, rids = await _text_field_with(client, ["false", "x", ""])
    await client.put(
        f"/api/table/{tid}/field/{fid}/convert", json={"type": "checkbox", "name": "F"}
    )
    assert await _cell(client, tid, rids[0], fid) is True
    assert await _cell(client, tid, rids[1], fid) is True
    assert await _cell(client, tid, rids[2], fid) is None


async def test_convert_text_to_rating_below_one_is_null(client):
    # rating cellValue is min 1; "0" (and negatives) coerce to null, "7" clamps
    # to the field max (5).
    tid, fid, rids = await _text_field_with(client, ["0", "7", "3"])
    await client.put(
        f"/api/table/{tid}/field/{fid}/convert",
        json={
            "type": "rating",
            "name": "F",
            "options": {"icon": "star", "color": "yellowBright", "max": 5},
        },
    )
    assert await _cell(client, tid, rids[0], fid) is None
    assert await _cell(client, tid, rids[1], fid) == 5
    assert await _cell(client, tid, rids[2], fid) == 3


async def test_convert_text_to_longtext_preserves_whitespace(client):
    # singleLineText -> longText is a basalConvert no-op: the stored column
    # carries over verbatim (surrounding whitespace kept, empty stays null).
    tid, fid, rids = await _text_field_with(client, ["  spaced  ", ""])
    await client.put(
        f"/api/table/{tid}/field/{fid}/convert", json={"type": "longText", "name": "F"}
    )
    assert await _cell(client, tid, rids[0], fid) == "  spaced  "
    assert await _cell(client, tid, rids[1], fid) is None


async def test_date_field_timezone_canonicalized_and_validated(client):
    tid, fid, _ = await _text_field_with(client, ["2024-01-15"])
    fmt = {"date": "YYYY-MM-DD", "time": "None"}

    # lowercase utc collapses to canonical UTC
    ok = await client.put(
        f"/api/table/{tid}/field/{fid}/convert",
        json={"type": "date", "name": "F", "options": {"formatting": {**fmt, "timeZone": "utc"}}},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["options"]["formatting"]["timeZone"] == "UTC"

    # a non-IANA zone is rejected with a 400 Invalid TimeZone
    bad = await client.put(
        f"/api/table/{tid}/field/{fid}/convert",
        json={"type": "date", "name": "F", "options": {"formatting": {**fmt, "timeZone": "gmt"}}},
    )
    assert bad.status_code == 400, bad.text
    assert bad.json()["message"] == "Invalid TimeZone: gmt"


async def _field_with(client, ftype, options, values):
    """A field of ``ftype`` carrying one record per value; returns
    (table_id, field_id, [record_id...])."""
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    base = await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"})
    tid = (await _create(client, f"/api/base/{base['id']}/table", {"name": "T1"}))["id"]
    body = {"type": ftype, "name": "F"}
    if options:
        body["options"] = options
    fid = (await _create(client, f"/api/table/{tid}/field", body))["id"]
    rids = []
    for v in values:
        rec = await _create(
            client, f"/api/table/{tid}/record",
            {"fieldKeyType": "id", "records": [{"fields": ({fid: v} if v is not None else {})}]},
        )
        rids.append(rec["records"][0]["id"])
    return tid, fid, rids


async def test_convert_checkbox_to_number_true_is_one(client):
    tid, fid, rids = await _field_with(client, "checkbox", {}, [True, False])
    await client.put(f"/api/table/{tid}/field/{fid}/convert", json={"type": "number", "name": "F"})
    assert await _cell(client, tid, rids[0], fid) == 1
    assert await _cell(client, tid, rids[1], fid) is None


async def test_convert_number_to_checkbox_zero_is_null(client):
    tid, fid, rids = await _field_with(client, "number", {}, [1, 0, -2])
    await client.put(
        f"/api/table/{tid}/field/{fid}/convert", json={"type": "checkbox", "name": "F"}
    )
    assert await _cell(client, tid, rids[0], fid) is True
    assert await _cell(client, tid, rids[1], fid) is None
    assert await _cell(client, tid, rids[2], fid) is True


async def test_convert_multiselect_to_singleselect_takes_first(client):
    opts = {"choices": [{"name": n} for n in ("a", "b", "c")]}
    tid, fid, rids = await _field_with(client, "multipleSelect", opts, [["a", "b"], ["c"]])
    vo = (await client.put(
        f"/api/table/{tid}/field/{fid}/convert",
        json={"type": "singleSelect", "name": "F", "options": {"choices": []}},
    )).json()
    assert sorted(ch["name"] for ch in vo["options"]["choices"]) == ["a", "b", "c"]
    assert await _cell(client, tid, rids[0], fid) == "a"
    assert await _cell(client, tid, rids[1], fid) == "c"
    # PLACEHOLDER_DATE_CONVERT


async def test_convert_date_to_number_is_epoch_millis(client):
    tid, fid, rids = await _field_with(
        client, "date",
        {"formatting": {"date": "YYYY-MM-DD", "time": "None", "timeZone": "UTC"}},
        ["2024-01-15T00:00:00.000Z"],
    )
    await client.put(f"/api/table/{tid}/field/{fid}/convert", json={"type": "number", "name": "F"})
    assert await _cell(client, tid, rids[0], fid) == 1705276800000


async def test_convert_date_to_text_is_pg_timestamptz(client):
    tid, fid, rids = await _field_with(
        client, "date",
        {"formatting": {"date": "YYYY-MM-DD", "time": "HH:mm", "timeZone": "UTC"}},
        ["2024-06-30T13:45:30.500Z"],
    )
    await client.put(
        f"/api/table/{tid}/field/{fid}/convert", json={"type": "singleLineText", "name": "F"}
    )
    assert await _cell(client, tid, rids[0], fid) == "2024-06-30 13:45:30.5+00"


async def test_convert_checkbox_to_rating_true_is_max(client):
    tid, fid, rids = await _field_with(client, "checkbox", {}, [True, False])
    await client.put(
        f"/api/table/{tid}/field/{fid}/convert",
        json={
            "type": "rating",
            "name": "F",
            "options": {"icon": "star", "color": "yellowBright", "max": 3},
        },
    )
    assert await _cell(client, tid, rids[0], fid) == 3
    assert await _cell(client, tid, rids[1], fid) is None


async def test_convert_number_to_date_reads_epoch_millis(client):
    tid, fid, rids = await _field_with(client, "number", {}, [1705276800000, 86400000])
    await client.put(
        f"/api/table/{tid}/field/{fid}/convert",
        json={"type": "date", "name": "F",
              "options": {"formatting": {"date": "YYYY-MM-DD", "time": "None", "timeZone": "UTC"}}},
    )
    assert await _cell(client, tid, rids[0], fid) == "2024-01-15T00:00:00.000Z"
    assert await _cell(client, tid, rids[1], fid) == "1970-01-02T00:00:00.000Z"


async def test_convert_date_to_rating_is_null(client):
    tid, fid, rids = await _field_with(
        client, "date",
        {"formatting": {"date": "YYYY-MM-DD", "time": "None", "timeZone": "UTC"}},
        ["2024-01-15T00:00:00.000Z"],
    )
    await client.put(
        f"/api/table/{tid}/field/{fid}/convert",
        json={
            "type": "rating",
            "name": "F",
            "options": {"icon": "star", "color": "yellowBright", "max": 5},
        },
    )
    assert await _cell(client, tid, rids[0], fid) is None
