"""Import analyze/status validation parity.

analyze reads absent query params as undefined (zod "received undefined" /
fileType enum error, not "received null"); status for a missing table 404s
with "Invalid tableId" before the shared 403 guard.
"""

from conftest import signup as _signup


async def test_analyze_missing_attachment_url(client):
    await _signup(client)
    resp = await client.get("/api/import/analyze", params={"fileType": "csv"})
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["code"] == "validation_error"
    assert body["message"] == (
        'Validation error: Invalid input: expected string, '
        'received undefined at "attachmentUrl"'
    )


async def test_analyze_missing_file_type(client):
    await _signup(client)
    resp = await client.get(
        "/api/import/analyze", params={"attachmentUrl": "https://x.com/f.csv"}
    )
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["code"] == "validation_error"
    assert body["message"] == (
        'Validation error: Invalid option: '
        'expected one of "csv"|"excel" at "fileType"'
    )


async def test_status_missing_table_invalid_tableid(client):
    await _signup(client)
    resp = await client.get("/api/import/status/tblZZZZZZZZZZZZZZZZ")
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["message"] == "Invalid tableId: tblZZZZZZZZZZZZZZZZ"
    assert body["code"] == "not_found"
    assert body["data"]["localization"]["i18nKey"] == "httpErrors.table.notFound"
