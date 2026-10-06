"""Endpoints under /api/table/{tableId} reject a missing table at the guard with
the reference's 403 "Table ID does not exist" (restricted_resource).
"""

from conftest import signup as _signup

_MISSING = "tblZZZZZZZZZZZZZZZZ"


async def _assert_guard(resp):
    assert resp.status_code == 403, resp.text
    body = resp.json()
    assert body["message"] == "Table ID does not exist"
    assert body["code"] == "restricted_resource"


async def test_missing_table_field_list(client):
    await _signup(client)
    await _assert_guard(await client.get(f"/api/table/{_MISSING}/field"))


async def test_missing_table_view_list(client):
    await _signup(client)
    await _assert_guard(await client.get(f"/api/table/{_MISSING}/view"))


async def test_missing_table_record_list(client):
    await _signup(client)
    await _assert_guard(await client.get(f"/api/table/{_MISSING}/record"))


async def test_missing_table_record_get(client):
    await _signup(client)
    await _assert_guard(await client.get(f"/api/table/{_MISSING}/record/recZZZZZZZZZZZZZZZZ"))
