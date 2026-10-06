"""Invalid-id (path-param) validation parity.

The reference's v2 domain id parsers (FieldId / RecordId / ViewId) reject a
malformed id with a `validation.invalid` domain error, surfaced as 400 with
data={domainCode, domainTags}. A few endpoints do NOT format-validate the id and
fall through to their natural lookup instead: field plan / convert return 404
notFoundInTable, record history returns 200 empty. Single-record update throws a
500 when the patch body carries no fields.
"""

from conftest import signup as _signup

_VDATA = {"domainCode": "validation.invalid", "domainTags": ["validation"]}
BAD = "badid"


async def _setup(client):
    await _signup(client)
    sid = (await client.post("/api/space", json={"name": "S"})).json()["id"]
    bid = (await client.post("/api/base", json={"spaceId": sid, "name": "B"})).json()["id"]
    tid = (await client.post(f"/api/base/{bid}/table", json={"name": "T1"})).json()["id"]
    fname = (await client.get(f"/api/table/{tid}/field")).json()[0]["name"]
    return tid, fname


def _assert_invalid(resp, kind: str):
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["message"] == f"Invalid {kind}"
    assert body["code"] == "validation_error"
    assert body["data"] == _VDATA


async def test_field_malformed_id_validates(client):
    tid, _ = await _setup(client)
    _assert_invalid(await client.get(f"/api/table/{tid}/field/{BAD}"), "FieldId")
    _assert_invalid(
        await client.get(f"/api/table/{tid}/field/{BAD}/filter-link-records"), "FieldId"
    )
    _assert_invalid(await client.delete(f"/api/table/{tid}/field/{BAD}"), "FieldId")
    _assert_invalid(
        await client.patch(f"/api/table/{tid}/field/{BAD}", json={"name": "x"}), "FieldId"
    )
    _assert_invalid(
        await client.post(f"/api/table/{tid}/field/{BAD}/duplicate", json={"name": "d"}),
        "FieldId",
    )
    _assert_invalid(
        await client.put(f"/api/table/{tid}/field/{BAD}/plan", json={"type": "singleLineText"}),
        "FieldId",
    )


async def test_field_plan_and_convert_skip_format_check(client):
    tid, _ = await _setup(client)
    for resp in (
        await client.get(f"/api/table/{tid}/field/{BAD}/plan"),
        await client.delete(f"/api/table/{tid}/field/{BAD}/plan"),
        await client.put(f"/api/table/{tid}/field/{BAD}/convert", json={"type": "singleLineText"}),
    ):
        assert resp.status_code == 404, resp.text
        body = resp.json()
        assert body["message"] == f"Field {BAD} not found in table {tid}"
        assert body["data"]["localization"]["i18nKey"] == "httpErrors.field.notFoundInTable"


async def test_record_malformed_id_validates(client):
    tid, fname = await _setup(client)
    _assert_invalid(await client.get(f"/api/table/{tid}/record/{BAD}"), "RecordId")
    _assert_invalid(await client.get(f"/api/table/{tid}/record/{BAD}/status"), "RecordId")
    _assert_invalid(await client.delete(f"/api/table/{tid}/record/{BAD}"), "RecordId")
    _assert_invalid(
        await client.post(f"/api/table/{tid}/record/{BAD}/duplicate", json={}), "RecordId"
    )
    # update format-check only fires once the patch carries at least one field
    _assert_invalid(
        await client.patch(
            f"/api/table/{tid}/record/{BAD}",
            json={"record": {"fields": {fname: "x"}}, "fieldKeyType": "name"},
        ),
        "RecordId",
    )


async def test_record_history_skips_format_check(client):
    tid, _ = await _setup(client)
    resp = await client.get(f"/api/table/{tid}/record/{BAD}/history")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"historyList": [], "userMap": {}}


async def test_update_empty_fields_is_500(client):
    tid, _ = await _setup(client)
    rid = (
        await client.post(
            f"/api/table/{tid}/record",
            json={"records": [{"fields": {}}], "fieldKeyType": "name"},
        )
    ).json()["records"][0]["id"]
    resp = await client.patch(
        f"/api/table/{tid}/record/{rid}",
        json={"record": {"fields": {}}, "fieldKeyType": "name"},
    )
    assert resp.status_code == 500, resp.text
    body = resp.json()
    assert body["message"] == "Internal server error"
    assert body["code"] == "internal_server_error"
    assert "data" not in body


async def test_view_malformed_id_validates(client):
    tid, _ = await _setup(client)
    _assert_invalid(await client.get(f"/api/table/{tid}/view/{BAD}"), "ViewId")
    _assert_invalid(
        await client.get(f"/api/table/{tid}/view/{BAD}/filter-link-records"), "ViewId"
    )
    _assert_invalid(await client.delete(f"/api/table/{tid}/view/{BAD}"), "ViewId")
    _assert_invalid(
        await client.put(f"/api/table/{tid}/view/{BAD}/name", json={"name": "x"}), "ViewId"
    )
    _assert_invalid(
        await client.put(f"/api/table/{tid}/view/{BAD}/filter", json={"filter": None}), "ViewId"
    )
    _assert_invalid(await client.post(f"/api/table/{tid}/view/{BAD}/duplicate", json={}), "ViewId")
