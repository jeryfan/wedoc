"""Undo/redo engine-header + stream parity.

A missing windowId is reported by the v2 engine upstream (engine header "v2",
body carries errorCode "validation.invalid"); an empty stack with a windowId
resolves on v1. Streams emit a bare "text/event-stream" content-type.
"""

import json

from conftest import signup as _signup

from wedoc.compat import undo_redo_engine_header

_ENGINE_HEADER = undo_redo_engine_header()


async def _table(client):
    await _signup(client)
    sid = (await client.post("/api/space", json={"name": "S"})).json()["id"]
    bid = (await client.post("/api/base", json={"spaceId": sid, "name": "A"})).json()["id"]
    return (await client.post(f"/api/base/{bid}/table", json={"name": "TA"})).json()["id"]


async def test_undo_empty_stack_reports_v1(client):
    tid = await _table(client)
    resp = await client.post(
        f"/api/table/{tid}/undo-redo/undo", headers={"x-window-id": "win1"}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json() == {"status": "empty"}
    assert resp.headers[_ENGINE_HEADER] == "v1"


async def test_undo_missing_window_reports_v2(client):
    tid = await _table(client)
    resp = await client.post(f"/api/table/{tid}/undo-redo/undo")
    assert resp.status_code == 201, resp.text
    assert resp.json() == {
        "status": "failed",
        "errorMessage": "Missing windowId for undo/redo operation",
        "errorCode": "validation.invalid",
    }
    assert resp.headers[_ENGINE_HEADER] == "v2"


async def test_undo_stream_missing_window_error_event(client):
    tid = await _table(client)
    resp = await client.post(f"/api/table/{tid}/undo-redo/undo-stream")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "text/event-stream"
    payload = json.loads(resp.text.strip().removeprefix("data: "))
    assert payload == {
        "id": "error",
        "mode": "undo",
        "engine": "v2",
        "message": "Missing windowId for undo/redo operation",
        "code": "validation.invalid",
    }


async def test_undo_stream_empty_done_event(client):
    tid = await _table(client)
    resp = await client.post(
        f"/api/table/{tid}/undo-redo/undo-stream", headers={"x-window-id": "win1"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "text/event-stream"
    payload = json.loads(resp.text.strip().removeprefix("data: "))
    assert payload == {"id": "done", "mode": "undo", "engine": "v1", "status": "empty"}
