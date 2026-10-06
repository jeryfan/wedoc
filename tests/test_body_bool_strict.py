"""JSON-body boolean fields are strict (z.boolean()), not lax-coerced.

record typecast and base withRecords reject a present non-boolean string with
"expected boolean, received string at <field>"; a real boolean still works.
"""

from conftest import signup as _signup


async def _record(client):
    await _signup(client)
    sid = (await client.post("/api/space", json={"name": "S"})).json()["id"]
    bid = (await client.post("/api/base", json={"spaceId": sid, "name": "B"})).json()["id"]
    tid = (await client.post(f"/api/base/{bid}/table", json={"name": "T1"})).json()["id"]
    nm = (await client.get(f"/api/table/{tid}/field")).json()[0]["name"]
    rid = (
        await client.post(
            f"/api/table/{tid}/record",
            json={"records": [{"fields": {nm: "x"}}], "fieldKeyType": "name"},
        )
    ).json()["records"][0]["id"]
    return sid, bid, tid, nm, rid


async def test_record_typecast_rejects_string(client):
    _, _, tid, nm, _ = await _record(client)
    bad = await client.post(
        f"/api/table/{tid}/record",
        json={"records": [{"fields": {nm: "y"}}], "fieldKeyType": "name", "typecast": "yes"},
    )
    assert bad.status_code == 400, bad.text
    assert bad.json()["message"] == (
        'Validation error: Invalid input: expected boolean, received string at "typecast"'
    )
    # a real boolean still passes
    ok = await client.post(
        f"/api/table/{tid}/record",
        json={"records": [{"fields": {nm: "z"}}], "fieldKeyType": "name", "typecast": True},
    )
    assert ok.status_code == 201, ok.text


async def test_base_duplicate_with_records_rejects_string(client):
    sid, bid, *_ = await _record(client)
    bad = await client.post(
        "/api/base/duplicate", json={"fromBaseId": bid, "spaceId": sid, "withRecords": "yes"}
    )
    assert bad.status_code == 400, bad.text
    assert bad.json()["message"] == (
        'Validation error: Invalid input: expected boolean, received string at "withRecords"'
    )


async def test_table_duplicate_include_records_rejects_string(client):
    _, bid, tid, *_ = await _record(client)
    bad = await client.post(
        f"/api/base/{bid}/table/{tid}/duplicate", json={"name": "D", "includeRecords": "yes"}
    )
    assert bad.status_code == 400, bad.text
    assert bad.json()["message"] == (
        'Validation error: Invalid input: expected boolean, received string at "includeRecords"'
    )


async def test_oauth_allow_device_flow_rejects_string(client):
    await _signup(client)
    bad = await client.post(
        "/api/oauth/client",
        json={
            "name": "A",
            "homepage": "https://ex.com",
            "redirectUris": ["https://ex.com/cb"],
            "allowDeviceFlow": "yes",
        },
    )
    assert bad.status_code == 400, bad.text
    assert bad.json()["message"] == (
        'Validation error: Invalid input: expected boolean, received string at "allowDeviceFlow"'
    )


async def test_plugin_auto_create_member_rejects_string(client):
    await _signup(client)
    bad = await client.post(
        "/api/plugin",
        json={"name": "P", "logo": "x", "positions": ["dashboard"], "autoCreateMember": "yes"},
    )
    assert bad.status_code == 400, bad.text
    assert bad.json()["message"] == (
        'Validation error: Invalid input: expected boolean, received string at "autoCreateMember"'
    )


async def test_base_share_copy_with_records_rejects_string(client):
    _, bid, *_ = await _record(client)
    node = (await client.get(f"/api/base/{bid}/node/list")).json()[0]["id"]
    shid = (await client.post(f"/api/base/{bid}/share", json={"nodeId": node})).json()["shareId"]
    sid = (await client.get(f"/api/base/{bid}")).json()["spaceId"]
    bad = await client.post(
        f"/api/share/{shid}/base/copy", json={"spaceId": sid, "withRecords": "yes"}
    )
    assert bad.status_code == 400, bad.text
    assert bad.json()["message"] == (
        'Validation error: Invalid input: expected boolean, received string at "withRecords"'
    )
