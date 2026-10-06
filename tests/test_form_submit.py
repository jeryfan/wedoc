"""form-submit creates a record through a form view: it stores link cells like
create (re-establishing the relation), 403s for a non-form view, and 404s for a
missing view.
"""

from conftest import signup as _signup


async def _create(client, path, body):
    resp = await client.post(path, json=body)
    assert resp.status_code in (200, 201), (path, resp.status_code, resp.text)
    return resp.json()


async def _setup(client):
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    bid = (await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"}))["id"]
    tb = (await _create(client, f"/api/base/{bid}/table", {"name": "TB"}))["id"]
    b1 = (await _create(client, f"/api/table/{tb}/record",
        {"fieldKeyType": "name", "records": [{"fields": {"Name": "b1"}}]}))["records"][0]["id"]
    ta = (await _create(client, f"/api/base/{bid}/table", {"name": "TA"}))["id"]
    lf = (await _create(client, f"/api/table/{ta}/field",
        {"name": "Link", "type": "link",
         "options": {"relationship": "manyOne", "foreignTableId": tb}}))["id"]
    name = next(f["id"] for f in (await client.get(f"/api/table/{ta}/field")).json()
                if f["name"] == "Name")
    grid = (await client.get(f"/api/table/{ta}/view")).json()[0]["id"]
    form = (await _create(client, f"/api/table/{ta}/view", {"name": "F", "type": "form"}))["id"]
    return ta, name, lf, b1, grid, form


async def test_form_submit_basic(client):
    ta, name, _lf, _b1, _grid, form = await _setup(client)
    resp = await client.post(f"/api/table/{ta}/record/form-submit",
        json={"viewId": form, "fields": {name: "hello"}})
    assert resp.status_code == 201, resp.text
    assert resp.json()["fields"][name] == "hello"


async def test_form_submit_link_cell(client):
    ta, _name, lf, b1, _grid, form = await _setup(client)
    resp = await client.post(f"/api/table/{ta}/record/form-submit",
        json={"viewId": form, "fields": {lf: {"id": b1}}})
    assert resp.status_code == 201, resp.text
    assert resp.json()["fields"][lf]["title"] == "b1"


async def test_form_submit_non_form_view(client):
    ta, name, _lf, _b1, grid, _form = await _setup(client)
    resp = await client.post(f"/api/table/{ta}/record/form-submit",
        json={"viewId": grid, "fields": {name: "x"}})
    assert resp.status_code == 403, resp.text
    assert resp.json()["message"] == "View is not a form"


async def test_form_submit_missing_view(client):
    ta, name, _lf, _b1, _grid, _form = await _setup(client)
    resp = await client.post(f"/api/table/{ta}/record/form-submit",
        json={"viewId": "viwZZZZZZZZZZZZZZZZ", "fields": {name: "x"}})
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["message"] == "View not found: viwZZZZZZZZZZZZZZZZ"
    assert body["data"]["domainCode"] == "view.not_found"
