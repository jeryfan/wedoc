"""Base duplicate: from-base not-found wording + record fidelity parity.

duplicateBase's findUniqueOrThrow surfaces a bare "Project {id} not found" (no
localization data), distinct from the generic base 404. withRecords copies rows.
"""

from conftest import signup as _signup


async def _base_with_rows(client):
    await _signup(client)
    sid = (await client.post("/api/space", json={"name": "S"})).json()["id"]
    bid = (await client.post("/api/base", json={"spaceId": sid, "name": "B"})).json()["id"]
    tid = (await client.post(f"/api/base/{bid}/table", json={"name": "T1"})).json()["id"]
    nm = (await client.get(f"/api/table/{tid}/field")).json()[0]["name"]
    for v in ("alpha", "bravo"):
        await client.post(
            f"/api/table/{tid}/record",
            json={"records": [{"fields": {nm: v}}], "fieldKeyType": "name"},
        )
    return sid, bid


async def test_duplicate_from_missing_is_bare_project_not_found(client):
    sid, _ = await _base_with_rows(client)
    resp = await client.post(
        "/api/base/duplicate",
        json={"fromBaseId": "bseZZZZZZZZZZZZZZZZ", "spaceId": sid, "withRecords": True},
    )
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["message"] == "Project bseZZZZZZZZZZZZZZZZ not found"
    assert "data" not in body


async def test_duplicate_with_records_copies_rows(client):
    sid, bid = await _base_with_rows(client)
    dup = await client.post(
        "/api/base/duplicate",
        json={"fromBaseId": bid, "spaceId": sid, "name": "Dup", "withRecords": True},
    )
    assert dup.status_code == 201, dup.text
    new_bid = dup.json()["id"]
    tables = (await client.get(f"/api/base/{new_bid}/table")).json()
    assert len(tables) == 1
    new_tid = tables[0]["id"]
    recs = (await client.get(f"/api/table/{new_tid}/record")).json()["records"]
    assert len(recs) == 2


async def test_duplicate_without_records_is_empty(client):
    sid, bid = await _base_with_rows(client)
    dup = await client.post(
        "/api/base/duplicate",
        json={"fromBaseId": bid, "spaceId": sid, "name": "Dup2", "withRecords": False},
    )
    assert dup.status_code == 201, dup.text
    new_tid = (await client.get(f"/api/base/{dup.json()['id']}/table")).json()[0]["id"]
    recs = (await client.get(f"/api/table/{new_tid}/record")).json()["records"]
    assert recs == []
