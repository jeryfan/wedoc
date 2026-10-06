"""Attachment signature/notify/read validation parity.

Ports the @Public attachments controller surface: the local-signature response
shape, the zod field errors (including z.nativeEnum(UploadType) reporting an
unquoted "Invalid option: expected one of 1|2|...|20"), the backend-only reject,
and the invalid-token / invalid-path envelopes.
"""

from conftest import signup as _signup

_TYPE_OPTIONS = "1|2|3|4|5|6|7|8|9|10|11|12|13|14|15|16|17|18|19|20"


async def test_signature_ok_shape(client):
    await _signup(client)
    resp = await client.post(
        "/api/attachments/signature",
        json={"contentType": "image/png", "contentLength": 100, "type": 2},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert sorted(body.keys()) == ["path", "requestHeaders", "token", "uploadMethod", "url"]
    assert body["uploadMethod"] == "PUT"
    assert body["url"] == f"/api/attachments/upload/{body['token']}"
    assert body["requestHeaders"] == {"Content-Type": "image/png", "Content-Length": 100}


async def test_signature_missing_content_type(client):
    await _signup(client)
    resp = await client.post(
        "/api/attachments/signature", json={"contentLength": 100, "type": 2}
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == (
        'Validation error: Invalid input: expected string, received undefined at "contentType"'
    )


async def test_signature_bad_type_native_enum(client):
    await _signup(client)
    resp = await client.post(
        "/api/attachments/signature",
        json={"contentType": "image/png", "contentLength": 100, "type": 99},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == (
        f'Validation error: Invalid option: expected one of {_TYPE_OPTIONS} at "type"'
    )


async def test_signature_missing_type_native_enum(client):
    await _signup(client)
    resp = await client.post(
        "/api/attachments/signature", json={"contentType": "image/png", "contentLength": 100}
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == (
        f'Validation error: Invalid option: expected one of {_TYPE_OPTIONS} at "type"'
    )


async def test_signature_backend_only_type(client):
    await _signup(client)
    resp = await client.post(
        "/api/attachments/signature",
        json={"contentType": "image/png", "contentLength": 100, "type": 15},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["message"] == "this upload type cannot be signed"


async def test_notify_invalid_token(client):
    await _signup(client)
    resp = await client.post("/api/attachments/notify/bogustoken123")
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["message"] == "Invalid token"
    assert body["data"]["localization"]["i18nKey"] == "httpErrors.attachment.invalidToken"


async def test_read_invalid_path(client):
    await _signup(client)
    resp = await client.get("/api/attachments/read/avatar/doesnotexist")
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["message"] == "Could not find attachment"
    assert body["data"]["localization"]["i18nKey"] == "httpErrors.attachment.invalidPath"
