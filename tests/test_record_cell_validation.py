"""Record writes validate cell values against the field's type (typecast off):
a wrong-type / out-of-range / unknown-option value is rejected with the
reference's 400 ``Invalid value for field "X": ✖ ...``.
"""

from conftest import signup as _signup


async def _create(client, path, body):
    resp = await client.post(path, json=body)
    assert resp.status_code in (200, 201), (path, resp.status_code, resp.text)
    return resp.json()


async def _table(client):
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    bid = (await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"}))["id"]
    return (await _create(client, f"/api/base/{bid}/table", {"name": "T"}))["id"]


async def _field(client, tid, body):
    return (await _create(client, f"/api/table/{tid}/field", body))["id"]


async def _write(client, tid, fid, value):
    body = {"fieldKeyType": "id", "records": [{"fields": {fid: value}}]}
    return await client.post(f"/api/table/{tid}/record", json=body)


async def test_wrong_type_and_range_rejected(client):
    tid = await _table(client)
    num = await _field(client, tid, {"name": "Num", "type": "number"})
    rat = await _field(client, tid, {"name": "Rat", "type": "rating",
        "options": {"icon": "star", "color": "yellowBright", "max": 5}})
    chk = await _field(client, tid, {"name": "Chk", "type": "checkbox", "options": {}})
    sel = await _field(client, tid, {"name": "Sel", "type": "singleSelect",
        "options": {"choices": [{"name": "a"}]}})

    r = await _write(client, tid, num, "abc")
    assert r.status_code == 400, r.text
    expected = (
        'Invalid value for field "Num": '
        "\u2716 Invalid input: expected number, received string"
    )
    assert r.json()["message"] == expected

    assert (await _write(client, tid, rat, 99)).status_code == 400
    assert (await _write(client, tid, rat, 0)).status_code == 400
    assert (await _write(client, tid, chk, "yes")).status_code == 400
    assert (await _write(client, tid, num, 12.5)).status_code == 201

    unknown = await _write(client, tid, sel, "nope")
    assert unknown.status_code == 400
    assert unknown.json()["data"]["domainCode"] == "validation.field.invalid_value"
    # an existing choice name is accepted
    assert (await _write(client, tid, sel, "a")).status_code == 201


async def _tc_write(client, tid, fid, value):
    body = {"fieldKeyType": "id", "typecast": True, "records": [{"fields": {fid: value}}]}
    return await client.post(f"/api/table/{tid}/record", json=body)


async def _cell(client, tid, rid, fid):
    r = await client.get(f"/api/table/{tid}/record/{rid}?fieldKeyType=id")
    return r.json()["fields"].get(fid)


async def test_typecast_coerces_on_write(client):
    tid = await _table(client)
    num = await _field(client, tid, {"name": "Num", "type": "number"})
    rat = await _field(client, tid, {"name": "Rat", "type": "rating",
        "options": {"icon": "star", "color": "yellowBright", "max": 5}})
    chk = await _field(client, tid, {"name": "Chk", "type": "checkbox", "options": {}})

    async def coerced(fid, value):
        r = await _tc_write(client, tid, fid, value)
        assert r.status_code == 201, r.text
        return await _cell(client, tid, r.json()["records"][0]["id"], fid)

    assert await coerced(num, "12") == 12
    assert await coerced(num, "abc") is None
    assert await coerced(rat, "9") == 5
    assert await coerced(rat, "0") is None
    assert await coerced(chk, "false") is None
    assert await coerced(chk, "x") is True
