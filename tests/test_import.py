"""Import endpoint tests — TableBaseScopeGuard parity for the PATCH routes.

Ports the guard behaviour of import-open-api.controller.ts: a table whose
baseId does not match the :baseId path param (or a missing table) 404s with
"Table {tableId} not found in project {baseId}" before the handler runs.
"""

from conftest import signup as _signup


async def _create(client, path, json):
    resp = await client.post(path, json=json)
    assert resp.status_code in (200, 201), (path, resp.status_code, resp.text)
    return resp.json()


async def test_import_patch_rejects_cross_base_table(client):
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    base_a = await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"})
    base_b = await _create(client, "/api/base", {"spaceId": space["id"], "name": "B"})
    table_a = await _create(client, f"/api/base/{base_a['id']}/table", {"name": "T1"})

    # a table belonging to base A cannot be imported into base B (cross-tenant write)
    resp = await client.patch(f"/api/import/{base_b['id']}/{table_a['id']}", json={})
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["message"] == f"Table {table_a['id']} not found in project {base_b['id']}"
    assert body["status"] == 404
    assert body["code"] == "not_found"
    assert body["data"]["localization"]["i18nKey"] == "httpErrors.notFound"


async def test_import_patch_rejects_unknown_table(client):
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    base = await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"})

    resp = await client.patch(f"/api/import/{base['id']}/tblDoesNotExist00", json={})
    assert resp.status_code == 404, resp.text
    assert resp.json()["message"] == f"Table tblDoesNotExist00 not found in project {base['id']}"


async def test_import_patch_stream_scope_guard_precedes_stream(client):
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    base_a = await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"})
    base_b = await _create(client, "/api/base", {"spaceId": space["id"], "name": "B"})
    table_a = await _create(client, f"/api/base/{base_a['id']}/table", {"name": "T1"})

    # the guard runs before the SSE handler, so this is a 404 JSON, not an error event
    resp = await client.patch(
        f"/api/import/{base_b['id']}/{table_a['id']}/stream", json={}
    )
    assert resp.status_code == 404, resp.text
    assert "text/event-stream" not in resp.headers.get("content-type", "")
    assert resp.json()["message"] == f"Table {table_a['id']} not found in project {base_b['id']}"


async def test_import_patch_same_base_passes_scope_guard(client):
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    base = await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"})
    table = await _create(client, f"/api/base/{base['id']}/table", {"name": "T1"})

    # in-base table clears the guard (reaches body validation, not a scope 404)
    resp = await client.patch(f"/api/import/{base['id']}/{table['id']}", json={})
    assert resp.status_code != 404, resp.text


async def test_analyze_rejects_internal_ip_url_ssrf(client):
    # SSRF guard: import fetches to private/link-local/loopback hosts are refused
    # (the reference blocks such meta-IP targets).
    await _signup(client)
    for host in ("169.254.169.254", "10.0.0.1"):
        resp = await client.get(
            "/api/import/analyze",
            params={"attachmentUrl": f"http://{host}/x.csv", "fileType": "csv"},
        )
        assert resp.status_code == 400, (host, resp.status_code, resp.text)
        assert "non-public address" in resp.json()["message"], resp.text
