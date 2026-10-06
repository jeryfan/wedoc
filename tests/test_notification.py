"""Notification endpoint parity: list/unread-count VO shapes + validation.

GET /api/notifications requires notifyStates (unread|read) and returns
{notifications, summary{critical,warning,info}}; unread-count returns
{unreadCount}. notifyStates/severity/notifyType are zod enums; status body
requires a boolean isRead.
"""

from conftest import signup as _signup

_STATES_MSG = (
    'Validation error: Invalid option: expected one of "unread"|"read" at "notifyStates"'
)


async def test_list_empty_shape(client):
    await _signup(client)
    resp = await client.get("/api/notifications", params={"notifyStates": "unread"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "notifications": [],
        "summary": {"critical": 0, "warning": 0, "info": 0},
    }


async def test_list_requires_notify_states(client):
    await _signup(client)
    resp = await client.get("/api/notifications")
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == _STATES_MSG


async def test_list_bad_states(client):
    await _signup(client)
    resp = await client.get("/api/notifications", params={"notifyStates": "bogus"})
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == _STATES_MSG


async def test_unread_count_shape(client):
    await _signup(client)
    resp = await client.get("/api/notifications/unread-count")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"unreadCount": 0}


async def test_read_all_ok(client):
    await _signup(client)
    resp = await client.patch("/api/notifications/read-all")
    assert resp.status_code == 200, resp.text


async def test_status_requires_boolean_is_read(client):
    await _signup(client)
    resp = await client.patch(
        "/api/notifications/notZZZZZZZZZZZZZZZZ/status", json={"isRead": "yes"}
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == (
        'Validation error: Invalid input: expected boolean, received string at "isRead"'
    )
