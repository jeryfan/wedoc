"""Selection copy / range-to-id / clear / paste parity.

Ports selection.controller.ts: range-to-id returnType enum (a null/absent value
reports "Invalid option", matching zod), the ranges query parsing (missing ->
400, malformed JSON -> 500 like the reference's unhandled parse, empty -> 400),
and the copy/clear/paste happy paths.
"""

from conftest import signup as _signup

_RT_OPTIONS = 'expected one of "recordId"|"fieldId"|"all"'
_RT_MSG = f'Validation error: Invalid option: {_RT_OPTIONS} at "returnType"'


async def _table_with_rows(client):
    await _signup(client)
    sid = (await client.post("/api/space", json={"name": "S"})).json()["id"]
    bid = (await client.post("/api/base", json={"spaceId": sid, "name": "A"})).json()["id"]
    tid = (await client.post(f"/api/base/{bid}/table", json={"name": "TA"})).json()["id"]
    for nm in ("apple", "banana"):
        await client.post(
            f"/api/table/{tid}/record",
            json={"fieldKeyType": "name", "records": [{"fields": {"Name": nm}}]},
        )
    return tid


async def test_copy_content_and_header(client):
    tid = await _table_with_rows(client)
    resp = await client.get(
        f"/api/table/{tid}/selection/copy", params={"ranges": "[[0,0],[2,1]]"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["content"] == "apple\t\t\nbanana\t\t"
    assert len(body["header"]) == 3


async def test_copy_header_field_instance_serialization(client):
    # the clipboard header serializes field instances: isMultipleCellValue is
    # always present (the field VO omits it when false), and a select instance
    # also exposes its empty choice-lookup caches.
    tid = await _table_with_rows(client)
    header = (
        await client.get(f"/api/table/{tid}/selection/copy", params={"ranges": "[[0,0],[2,1]]"})
    ).json()["header"]
    for field in header:
        assert field["isMultipleCellValue"] is False
    status = next(f for f in header if f["type"] == "singleSelect")
    assert status["_innerChoicesMap"] == {}
    assert status["_innerChoicesMapKey"] == ""


async def test_range_to_id_all(client):
    tid = await _table_with_rows(client)
    resp = await client.get(
        f"/api/table/{tid}/selection/range-to-id",
        params={"ranges": "[[0,0],[2,1]]", "returnType": "all"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["fieldIds"]) == 3


async def test_range_to_id_bad_return_type(client):
    tid = await _table_with_rows(client)
    resp = await client.get(
        f"/api/table/{tid}/selection/range-to-id",
        params={"ranges": "[[0,0],[2,1]]", "returnType": "bogus"},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == _RT_MSG


async def test_range_to_id_missing_return_type(client):
    tid = await _table_with_rows(client)
    resp = await client.get(
        f"/api/table/{tid}/selection/range-to-id", params={"ranges": "[[0,0],[2,1]]"}
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == _RT_MSG


async def test_copy_missing_ranges(client):
    tid = await _table_with_rows(client)
    resp = await client.get(f"/api/table/{tid}/selection/copy")
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == (
        'Validation error: Invalid input: expected string, received undefined at "ranges"'
    )


async def test_copy_malformed_ranges_500(client):
    tid = await _table_with_rows(client)
    resp = await client.get(f"/api/table/{tid}/selection/copy", params={"ranges": "notjson"})
    assert resp.status_code == 500, resp.text


async def test_copy_empty_ranges(client):
    tid = await _table_with_rows(client)
    resp = await client.get(f"/api/table/{tid}/selection/copy", params={"ranges": "[]"})
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == (
        'Validation error: The range parameter must be a valid 2D array with even length. '
        'at "ranges"'
    )


async def test_clear_and_paste(client):
    tid = await _table_with_rows(client)
    cleared = await client.patch(
        f"/api/table/{tid}/selection/clear", json={"ranges": [[0, 0], [0, 0]]}
    )
    assert cleared.status_code == 200, cleared.text
    pasted = await client.patch(
        f"/api/table/{tid}/selection/paste",
        json={"ranges": [[0, 0], [0, 0]], "content": "grape"},
    )
    assert pasted.status_code == 200, pasted.text
    assert pasted.json() == {"ranges": [[0, 0], [0, 0]]}
