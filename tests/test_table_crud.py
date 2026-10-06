"""Table CRUD parity: create VO shape, not-found (project wording), default
view id, and the db-table-name format validation.
"""

from conftest import signup as _signup


async def _base(client):
    await _signup(client)
    sid = (await client.post("/api/space", json={"name": "S"})).json()["id"]
    return (await client.post("/api/base", json={"spaceId": sid, "name": "B"})).json()["id"]


async def test_create_table_shape(client):
    bid = await _base(client)
    resp = await client.post(f"/api/base/{bid}/table", json={"name": "T1"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert sorted(body.keys()) == [
        "dbTableName",
        "defaultViewId",
        "fields",
        "id",
        "name",
        "records",
        "views",
    ]
    assert body["records"] == []
    assert len(body["views"]) == 1
    assert body["views"][0]["type"] == "grid"


async def test_get_table_missing(client):
    bid = await _base(client)
    resp = await client.get(f"/api/base/{bid}/table/tblZZZZZZZZZZZZZZZZ")
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["message"] == f"Table tblZZZZZZZZZZZZZZZZ not found in project {bid}"
    assert body["data"]["localization"]["i18nKey"] == "httpErrors.notFound"


async def test_default_view_id(client):
    bid = await _base(client)
    tid = (await client.post(f"/api/base/{bid}/table", json={"name": "T1"})).json()["id"]
    resp = await client.get(f"/api/base/{bid}/table/{tid}/default-view-id")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"].startswith("viw")


async def test_db_table_name_bad_format(client):
    bid = await _base(client)
    tid = (await client.post(f"/api/base/{bid}/table", json={"name": "T1"})).json()["id"]
    resp = await client.put(
        f"/api/base/{bid}/table/{tid}/db-table-name", json={"dbTableName": "1bad-name!"}
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == 'Validation error: Invalid name format at "dbTableName"'
