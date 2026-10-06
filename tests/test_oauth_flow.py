"""OAuth authorization-code flow stamps the client secret's lastUsedTime.

Drives create-client -> secret -> authorize -> decision -> access_token, then
verifies the used secret reports lastUsedTime (the client-password strategy
stamps it on each successful authentication), matching the reference.
"""

from conftest import signup as _signup


async def test_client_secret_last_used_time_populated_after_token(client):
    await _signup(client)
    payload = {
        "name": "LU test",
        "homepage": "https://example.com",
        "scopes": ["table|read"],
        "redirectUris": ["https://example.com/callback"],
    }
    cid = (await client.post("/api/oauth/client", json=payload)).json()["clientId"]
    secret = (await client.post(f"/api/oauth/client/{cid}/secret")).json()["secret"]

    # before use: no lastUsedTime
    before = (await client.get(f"/api/oauth/client/{cid}")).json()["secrets"][0]
    assert "lastUsedTime" not in before or before.get("lastUsedTime") is None

    # authorize -> 302 to /oauth/decision?transaction_id=...
    auth = await client.get(
        "/api/oauth/authorize",
        params={
            "client_id": cid,
            "response_type": "code",
            "redirect_uri": "https://example.com/callback",
            "scope": "table|read",
            "state": "xyz",
        },
        follow_redirects=False,
    )
    assert auth.status_code == 302, auth.text
    txn = auth.headers["location"].split("transaction_id=", 1)[1].split("&", 1)[0]

    # decision (allow) -> 302 to redirect_uri?code=...
    dec = await client.post(
        "/api/oauth/decision", data={"transaction_id": txn}, follow_redirects=False
    )
    assert dec.status_code == 302, dec.text
    code = dec.headers["location"].split("code=", 1)[1].split("&", 1)[0]

    # exchange the code with the client secret -> token
    tok = await client.post(
        "/api/oauth/access_token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "https://example.com/callback",
            "client_id": cid,
            "client_secret": secret,
        },
    )
    assert tok.status_code == 201, tok.text
    assert tok.json()["token_type"] == "Bearer"

    # after use: the used secret now reports lastUsedTime
    after = (await client.get(f"/api/oauth/client/{cid}")).json()["secrets"][0]
    assert after.get("lastUsedTime"), after


async def test_create_client_invalid_redirect_uri_reports_index(client):
    # z.array(z.string().url()): a bad element reports its array index.
    await _signup(client)
    resp = await client.post(
        "/api/oauth/client",
        json={
            "name": "X",
            "homepage": "https://example.com",
            "scopes": ["table|read"],
            "redirectUris": ["https://example.com/cb", "notaurl"],
        },
    )
    assert resp.status_code == 400
    assert resp.json()["message"] == 'Validation error: Invalid URL at "redirectUris[1]"'
