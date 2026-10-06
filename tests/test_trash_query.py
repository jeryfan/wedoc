"""Trash /items pageSize query validation matches the reference's zod messages."""

from conftest import signup as _signup


async def _base(client):
    await _signup(client)
    space = await client.post("/api/space", json={"name": "S"})
    sid = space.json()["id"]
    return (await client.post("/api/base", json={"spaceId": sid, "name": "A"})).json()["id"]


async def test_items_pagesize_non_integer(client):
    bid = await _base(client)
    resp = await client.get("/api/trash/items", params={"resourceType": "base", "resourceId": bid,
                                                         "pageSize": "abc"})
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == (
        'Validation error: Invalid input: expected number, received NaN at "pageSize"'
    )


async def test_items_pagesize_too_big(client):
    bid = await _base(client)
    resp = await client.get("/api/trash/items", params={"resourceType": "base", "resourceId": bid,
                                                         "pageSize": "999"})
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == (
        'Validation error: Too big: expected number to be <=20 at "pageSize"'
    )
