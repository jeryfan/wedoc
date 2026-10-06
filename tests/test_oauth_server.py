"""OAuth authorization-server parity (device grant + token endpoint).

Ports oauth-server.controller.ts + oauth-device.service.ts: the device-code
issuance shape, the RFC 6749 error envelopes ({error, error_description}), and
the HTTP error envelopes for client auth / grant handling.
"""

from conftest import signup as _signup


async def _client_with_device_flow(client):
    await _signup(client)
    created = await client.post(
        "/api/oauth/client",
        json={
            "name": "OS",
            "homepage": "https://x.com",
            "redirectUris": ["https://x.com/cb"],
            "allowDeviceFlow": True,
        },
    )
    assert created.status_code == 201, created.text
    cid = created.json()["clientId"]
    secret = (await client.post(f"/api/oauth/client/{cid}/secret")).json()["secret"]
    return cid, secret


async def test_device_code_missing_client(client):
    await _signup(client)
    resp = await client.post("/api/oauth/device/code", data={})
    assert resp.status_code == 400, resp.text
    assert resp.json() == {
        "error": "invalid_request",
        "error_description": "client_id is required",
    }


async def test_device_code_unknown_client(client):
    await _signup(client)
    resp = await client.post("/api/oauth/device/code", data={"client_id": "bogusclient"})
    assert resp.status_code == 400, resp.text
    assert resp.json() == {"error": "invalid_client", "error_description": "Unknown client"}


async def test_access_token_missing_client(client):
    await _signup(client)
    resp = await client.post("/api/oauth/access_token", data={})
    assert resp.status_code == 401, resp.text
    assert resp.json()["message"] == "Unauthorized"


async def test_access_token_unknown_client(client):
    await _signup(client)
    resp = await client.post(
        "/api/oauth/access_token", data={"client_id": "bogus", "client_secret": "x"}
    )
    assert resp.status_code == 401, resp.text
    assert resp.json()["message"] == "Client not found"


async def test_device_code_issued_shape(client):
    cid, _ = await _client_with_device_flow(client)
    resp = await client.post("/api/oauth/device/code", data={"client_id": cid})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert sorted(body.keys()) == [
        "device_code",
        "expires_in",
        "interval",
        "user_code",
        "verification_uri",
    ]
    assert body["expires_in"] == 900
    assert body["interval"] == 5
    assert body["verification_uri"].endswith("/oauth/device")


async def test_access_token_unknown_grant_type(client):
    cid, secret = await _client_with_device_flow(client)
    resp = await client.post(
        "/api/oauth/access_token", data={"client_id": cid, "client_secret": secret}
    )
    assert resp.status_code == 500, resp.text


async def test_access_token_bad_authorization_code(client):
    cid, secret = await _client_with_device_flow(client)
    resp = await client.post(
        "/api/oauth/access_token",
        data={
            "client_id": cid,
            "client_secret": secret,
            "grant_type": "authorization_code",
            "code": "badcode",
            "redirect_uri": "https://x.com/cb",
        },
    )
    assert resp.status_code == 401, resp.text
    assert resp.json()["message"] == "Invalid code"


async def test_access_token_bad_device_code(client):
    cid, secret = await _client_with_device_flow(client)
    resp = await client.post(
        "/api/oauth/access_token",
        data={
            "client_id": cid,
            "client_secret": secret,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": "baddevice",
        },
    )
    assert resp.status_code == 400, resp.text
    assert resp.json() == {"error": "expired_token"}
