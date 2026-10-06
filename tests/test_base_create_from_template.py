"""Base create-from-template error-surface parity.

templateId is required (missing -> 400 zod). An unknown or empty templateId
reaches findUniqueOrThrow and surfaces a bare 500 "Internal Server Error"
(the community build has no seeded templates to create from).
"""

from conftest import signup as _signup


async def _space(client):
    await _signup(client)
    return (await client.post("/api/space", json={"name": "S"})).json()["id"]


async def test_missing_template_id_is_400(client):
    sid = await _space(client)
    resp = await client.post("/api/base/create-from-template", json={"spaceId": sid})
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == (
        'Validation error: Invalid input: expected string, received undefined at "templateId"'
    )


async def test_unknown_template_id_is_500(client):
    sid = await _space(client)
    resp = await client.post(
        "/api/base/create-from-template",
        json={"spaceId": sid, "templateId": "tplZZZZZZZZZZZZZZZZ"},
    )
    assert resp.status_code == 500, resp.text
    assert resp.json()["message"] == "Internal Server Error"


async def test_empty_template_id_is_500(client):
    sid = await _space(client)
    resp = await client.post(
        "/api/base/create-from-template", json={"spaceId": sid, "templateId": ""}
    )
    assert resp.status_code == 500, resp.text
    assert resp.json()["message"] == "Internal Server Error"
