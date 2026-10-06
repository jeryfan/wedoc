"""Record duplicate not-found data-shape parity.

The duplicate path surfaces the localized record not-found (i18nKey
httpErrors.record.notFound), not the v2 domain-error shape (record.not_found)
used by the update path.
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
            json={"records": [{"fields": {nm: "alpha"}}], "fieldKeyType": "name"},
        )
    ).json()["records"][0]["id"]
    return tid, rid


async def test_duplicate_missing_record_uses_localization(client):
    tid, _ = await _record(client)
    resp = await client.post(
        f"/api/table/{tid}/record/recZZZZZZZZZZZZZZZZ/duplicate", json={}
    )
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["message"] == "Record not found"
    assert body["data"] == {"localization": {"i18nKey": "httpErrors.record.notFound"}}


async def test_duplicate_existing_record_creates_copy(client):
    tid, rid = await _record(client)
    resp = await client.post(f"/api/table/{tid}/record/{rid}/duplicate", json={})
    assert resp.status_code == 201, resp.text
    assert resp.json()["id"] != rid
    recs = (await client.get(f"/api/table/{tid}/record")).json()["records"]
    assert len(recs) == 2
