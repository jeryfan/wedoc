"""Pin order serialization parity.

pin.order is a Double column but JSON.stringify renders whole values without a
trailing .0, so add/list return integer orders (1, 2), not 1.0/2.0.
"""

from conftest import signup as _signup


async def _space_base(client):
    await _signup(client)
    sid = (await client.post("/api/space", json={"name": "S"})).json()["id"]
    bid = (await client.post("/api/base", json={"spaceId": sid, "name": "B"})).json()["id"]
    return sid, bid


async def test_pin_order_is_integer_when_whole(client):
    sid, bid = await _space_base(client)
    r1 = await client.post("/api/pin", json={"type": "base", "id": bid})
    assert r1.status_code == 201, r1.text
    assert r1.json()["order"] == 1
    assert isinstance(r1.json()["order"], int)

    r2 = await client.post("/api/pin", json={"type": "space", "id": sid})
    assert r2.json()["order"] == 2
    assert isinstance(r2.json()["order"], int)

    listed = await client.get("/api/pin/list")
    assert listed.status_code == 200, listed.text
    orders = [e["order"] for e in listed.json()]
    assert orders == [1, 2]
    assert all(isinstance(o, int) for o in orders)
