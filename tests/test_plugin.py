"""Plugin create/validation/not-found parity.

Ports plugin.controller.ts contract: zod field errors, the created plugin
shape (status "developing"), and the "Plugin not found" 404.
"""

from conftest import signup as _signup


async def test_plugin_list_empty(client):
    await _signup(client)
    resp = await client.get("/api/plugin")
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


async def test_plugin_create_validation(client):
    await _signup(client)
    cases = [
        (
            {"logo": "https://x.com/l.png", "positions": ["dashboard"]},
            'Validation error: Invalid input: expected string, received undefined at "name"',
        ),
        (
            {"name": "P1", "positions": ["dashboard"]},
            'Validation error: Invalid input: expected string, received undefined at "logo"',
        ),
        (
            {"name": "P1", "logo": "https://x.com/l.png"},
            'Validation error: Invalid input: expected array, received undefined at "positions"',
        ),
        (
            {"name": "P1", "logo": "https://x.com/l.png", "positions": ["nope"]},
            'Validation error: Invalid option: expected one of '
            '"dashboard"|"view"|"contextMenu"|"panel" at "positions[0]"',
        ),
        (
            {"name": "P1", "logo": "https://x.com/l.png", "positions": []},
            'Validation error: Too small: expected array to have >=1 items at "positions"',
        ),
        (
            {"name": "x" * 21, "logo": "https://x.com/l.png", "positions": ["dashboard"]},
            'Validation error: Too big: expected string to have <=20 characters at "name"',
        ),
    ]
    for payload, message in cases:
        resp = await client.post("/api/plugin", json=payload)
        assert resp.status_code == 400, (payload, resp.text)
        assert resp.json()["message"] == message


async def test_plugin_create_shape(client):
    await _signup(client)
    resp = await client.post(
        "/api/plugin",
        json={"name": "P1", "logo": "https://x.com/l.png", "positions": ["dashboard"]},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert sorted(body.keys()) == [
        "createdTime",
        "id",
        "logo",
        "name",
        "positions",
        "secret",
        "status",
    ]
    assert body["name"] == "P1"
    assert body["positions"] == ["dashboard"]
    assert body["status"] == "developing"


async def test_plugin_get_missing(client):
    await _signup(client)
    resp = await client.get("/api/plugin/plgZZZZZZZZZZZZZZZZ")
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["message"] == "Plugin not found"
    assert body["data"]["localization"]["i18nKey"] == "httpErrors.plugin.notFound"
