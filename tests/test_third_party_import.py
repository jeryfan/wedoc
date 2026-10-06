"""Airtable / Google-Sheet import parity.

Ports the controller contract: picker-config 404 when unconfigured, the zod
superRefine cross-field messages, and the import stream (plain POST -> 201)
that emits an initial {"type":"progress","phase":"fetching_schema"} event.
The real vendor pull is deferred, so the stream ends with an error event.
"""

import json

from conftest import signup as _signup


async def test_picker_config_unconfigured(client):
    await _signup(client)
    resp = await client.get("/api/base/import-google-sheet/picker-config")
    assert resp.status_code == 404, resp.text
    assert resp.json()["message"] == "Google Sheets import is not configured on this instance"


async def test_airtable_analyze_requires_credentials(client):
    await _signup(client)
    resp = await client.post("/api/base/import-airtable/analyze", json={"airtableBaseId": "app"})
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == (
        'Validation error: Either integrationId or accessToken is required at "integrationId"'
    )


async def test_google_analyze_requires_spreadsheet_id(client):
    await _signup(client)
    resp = await client.post("/api/base/import-google-sheet/analyze", json={"accessToken": "x"})
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == (
        'Validation error: Invalid input: expected string, received undefined at "spreadsheetId"'
    )


async def test_airtable_stream_refine_collects_issues(client):
    await _signup(client)
    resp = await client.post("/api/base/import-airtable/stream", json={"airtableBaseId": "app"})
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == (
        'Validation error: Either integrationId or accessToken is required at "integrationId"; '
        'BaseName is required when baseId is not provided. at "baseName"; '
        'SpaceId is required when baseId is not provided. at "spaceId"'
    )


async def test_google_stream_refine_collects_issues(client):
    await _signup(client)
    resp = await client.post(
        "/api/base/import-google-sheet/stream",
        json={"spreadsheetId": "spr", "accessToken": "x"},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == (
        'Validation error: BaseName is required when baseId is not provided. at "baseName"; '
        'SpaceId is required when baseId is not provided. at "spaceId"'
    )


async def test_airtable_stream_emits_progress_then_error(client):
    await _signup(client)
    sid = (await client.post("/api/space", json={"name": "S"})).json()["id"]
    # integrationId (no accessToken) hits the deferred path without any vendor call
    resp = await client.post(
        "/api/base/import-airtable/stream",
        json={"airtableBaseId": "app", "integrationId": "int", "spaceId": sid, "baseName": "B"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.headers["content-type"].split(";")[0] == "text/event-stream"
    events = [json.loads(chunk[len("data: ") :]) for chunk in resp.text.strip().split("\n\n")]
    assert events[0] == {"type": "progress", "phase": "fetching_schema"}
    assert events[-1]["type"] == "error"
