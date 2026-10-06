"""Share-view collaborators fieldId-required error parity.

For a grid share view, GET /api/share/{shareId}/view/collaborators without a
fieldId is a domain validation error carrying
data={domainCode: "view_collaborators.field_required", domainTags: ["validation"]}.
"""

from conftest import signup as _signup


async def test_collaborators_field_required_domain_error(client):
    await _signup(client)
    sid = (await client.post("/api/space", json={"name": "S"})).json()["id"]
    bid = (await client.post("/api/base", json={"spaceId": sid, "name": "B"})).json()["id"]
    tid = (await client.post(f"/api/base/{bid}/table", json={"name": "T1"})).json()["id"]
    vid = (await client.get(f"/api/table/{tid}/view")).json()[0]["id"]
    shid = (await client.post(f"/api/table/{tid}/view/{vid}/enable-share")).json()["shareId"]

    resp = await client.get(f"/api/share/{shid}/view/collaborators")
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["message"] == "fieldId is required"
    assert body["data"] == {
        "domainCode": "view_collaborators.field_required",
        "domainTags": ["validation"],
    }
