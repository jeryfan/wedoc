"""Route-not-found message parity.

Express `Cannot <METHOD> <originalUrl>` includes the raw query string, so an
unmatched /api route echoes the query in the 404 message.
"""

from conftest import signup as _signup


async def test_not_found_includes_query_string(client):
    await _signup(client)

    got = await client.get("/api/zzz/nope?foo=bar&x=1")
    assert got.status_code == 404, got.text
    assert got.json()["message"] == "Cannot GET /api/zzz/nope?foo=bar&x=1"

    posted = await client.post("/api/zzz/nope?a=b", json={})
    assert posted.status_code == 404, posted.text
    assert posted.json()["message"] == "Cannot POST /api/zzz/nope?a=b"

    plain = await client.get("/api/zzz/nope")
    assert plain.status_code == 404, plain.text
    assert plain.json()["message"] == "Cannot GET /api/zzz/nope"
