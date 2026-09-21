"""Endpoint tests for /api/user.

Same environment contract as tests/test_auth.py (dev PG/Redis, unique rows per
run, skipped when infra is down).
"""

import io
import os

os.environ.setdefault("PUBLIC_ORIGIN", "http://localhost:3000")
os.environ.setdefault("STORAGE_PREFIX", "http://localhost:3000")
os.environ.setdefault("BACKEND_CACHE_REDIS_URI", "redis://default:wedoc@127.0.0.1:46380")
os.environ.setdefault("PRISMA_DATABASE_URL", "postgresql://wedoc:wedoc@127.0.0.1:42346/wedoc")
os.environ.setdefault("SECRET_KEY", "refSecretKey000000")

import httpx
from conftest import signup as _signup

# `client` / `db` fixtures are auto-registered from conftest.py


async def _signed_in(client: httpx.AsyncClient):
    email, resp = await _signup(client)
    return email, resp.json()["id"]


async def test_update_name(client):
    await _signed_in(client)
    resp = await client.patch("/api/user/name", json={"name": "New Name"})
    assert resp.status_code == 200
    assert resp.content == b""
    me = await client.get("/api/auth/user/me")
    assert me.json()["name"] == "New Name"

    resp = await client.patch("/api/user/name", json={"name": ""})
    assert resp.status_code == 400
    assert resp.json()["message"] == (
        'Validation error: Too small: expected string to have >=1 characters at "name"'
    )
    resp = await client.patch("/api/user/name", json={"name": "x" * 101})
    assert resp.status_code == 400
    assert "<=100" in resp.json()["message"]
    resp = await client.patch("/api/user/name", json={})
    assert resp.json()["message"] == (
        'Validation error: Invalid input: expected string, received undefined at "name"'
    )


async def test_update_lang(client, db):
    _, user_id = await _signed_in(client)
    resp = await client.patch("/api/user/lang", json={"lang": "zh"})
    assert resp.status_code == 200
    me = await client.get("/api/auth/user/me")
    assert me.json()["lang"] == "zh"
    row = await db.fetchrow("SELECT lang FROM users WHERE id=$1", user_id)
    assert row["lang"] == "zh"


async def test_update_notify_meta_merges(client, db):
    await _signed_in(client)
    resp = await client.patch("/api/user/notify-meta", json={"email": False})
    assert resp.status_code == 200
    me = await client.get("/api/auth/user/me")
    assert me.json()["notifyMeta"] == {"email": False}

    resp = await client.patch("/api/user/notify-meta", json={"appBuilderChatIntroDismissed": True})
    assert resp.status_code == 200
    me = await client.get("/api/auth/user/me")
    assert me.json()["notifyMeta"] == {"email": False, "appBuilderChatIntroDismissed": True}

    resp = await client.patch("/api/user/notify-meta", json={"email": "yes"})
    assert resp.status_code == 400
    assert resp.json()["message"] == (
        'Validation error: Invalid input: expected boolean, received string at "email"'
    )


async def test_track(client):
    await _signed_in(client)
    resp = await client.post("/api/user/track", json={"event": "app.view", "properties": {"a": 1}})
    assert resp.status_code == 204
    assert resp.content == b""
    # non-whitelisted events are still 204
    resp = await client.post("/api/user/track", json={"event": "custom.thing"})
    assert resp.status_code == 204
    resp = await client.post("/api/user/track", json={"event": ""})
    assert resp.status_code == 400


def _png_bytes(width: int = 200, height: int = 100) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), (200, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()


async def test_update_avatar(client, db):
    _, user_id = await _signed_in(client)
    resp = await client.patch(
        "/api/user/avatar", files={"file": ("a.png", _png_bytes(), "image/png")}
    )
    assert resp.status_code == 200
    me = await client.get("/api/auth/user/me")
    avatar = me.json()["avatar"]
    assert avatar.startswith(
        f"http://localhost:3000/api/attachments/read/public/avatar/{user_id}?v="
    )

    attachment = await db.fetchrow("SELECT * FROM attachments WHERE token=$1", user_id)
    assert attachment["mimetype"] == "image/webp"
    assert attachment["path"] == f"avatar/{user_id}"

    resp = await client.patch(
        "/api/user/avatar", files={"file": ("a.txt", b"hello", "text/plain")}
    )
    assert resp.status_code == 400
    assert resp.json()["message"] == "Unsupported file type. Only JPEG, PNG, and WebP are allowed."

    resp = await client.patch(
        "/api/user/avatar", files={"file": ("a.png", b"not a png", "image/png")}
    )
    assert resp.status_code == 400
    assert resp.json()["message"] == "Unsupported file type"
    assert resp.json()["data"]["localization"]["i18nKey"] == "httpErrors.attachment.invalidImage"

    resp = await client.patch("/api/user/avatar")
    assert resp.status_code == 500


async def test_last_visit_flow(client, db):
    _email, user_id = await _signed_in(client)
    # seed a space/base/table/view + ownership for the visit queries
    await db.execute(
        "INSERT INTO space (id, name, created_by) VALUES ('spcT2', 'S', $1) "
        "ON CONFLICT (id) DO NOTHING",
        user_id,
    )
    await db.execute(
        "INSERT INTO base (id, space_id, name, \"order\", created_by) VALUES "
        "('bseT2', 'spcT2', 'B', 1, $1) ON CONFLICT (id) DO NOTHING",
        user_id,
    )
    await db.execute(
        "INSERT INTO collaborator (id, resource_type, resource_id, principal_type,"
        " principal_id, role_name, created_by) "
        "VALUES ('collabT2', 'space', 'spcT2', 'user', $1, 'owner', $1) ON CONFLICT DO NOTHING",
        user_id,
    )
    await db.execute(
        "INSERT INTO table_meta (id, base_id, name, db_table_name, \"order\", created_by, version) "
        "VALUES ('tblT2', 'bseT2', 'T', 'tT2', 1, $1, 1) ON CONFLICT (id) DO NOTHING",
        user_id,
    )
    await db.execute(
        "INSERT INTO view (id, table_id, name, type, column_meta, \"order\", version, created_by) "
        "VALUES ('viwT2a', 'tblT2', 'v1', 'grid', '{}', 1, 1, $1) ON CONFLICT (id) DO NOTHING",
        user_id,
    )
    await db.execute(
        "INSERT INTO view (id, table_id, name, type, column_meta, \"order\", version, created_by) "
        "VALUES ('viwT2b', 'tblT2', 'v2', 'grid', '{}', 2, 1, $1) ON CONFLICT (id) DO NOTHING",
        user_id,
    )
    try:
        # default: first table + first view
        resp = await client.get(
            "/api/user/last-visit",
            params={"resourceType": "table", "parentResourceId": "bseT2"},
        )
        assert resp.json() == {
            "resourceId": "tblT2",
            "childResourceId": "viwT2a",
            "resourceType": "table",
        }

        resp = await client.post(
            "/api/user/last-visit",
            json={
                "resourceType": "table",
                "resourceId": "tblT2",
                "parentResourceId": "bseT2",
                "childResourceId": "viwT2b",
            },
        )
        assert resp.status_code == 201
        assert resp.content == b""

        resp = await client.get(
            "/api/user/last-visit",
            params={"resourceType": "table", "parentResourceId": "bseT2"},
        )
        assert resp.json()["childResourceId"] == "viwT2b"

        resp = await client.get(
            "/api/user/last-visit",
            params={"resourceType": "view", "parentResourceId": "tblT2"},
        )
        assert resp.json() == {"resourceId": "viwT2b", "resourceType": "view"}

        resp = await client.post(
            "/api/user/last-visit",
            json={"resourceType": "base", "resourceId": "bseT2", "parentResourceId": "spcT2"},
        )
        assert resp.status_code == 201

        resp = await client.get(
            "/api/user/last-visit/map",
            params={"resourceType": "base", "parentResourceId": "bseT2"},
        )
        # visited entries carry no resourceType key (upstream raw SQL selects
        # only resourceId/parentResourceId)
        assert resp.json() == {"tblT2": {"resourceId": "viwT2b", "parentResourceId": "tblT2"}}

        resp = await client.get("/api/user/last-visit/list-base")
        body = resp.json()
        assert body["total"] == 1
        item = body["list"][0]
        assert item["resourceId"] == "bseT2"
        assert item["resource"] == {
            "id": "bseT2",
            "name": "B",
            "icon": None,
            "role": "owner",
            "spaceId": "spcT2",
            "createdBy": user_id,
        }

        resp = await client.get(
            "/api/user/last-visit/base-node", params={"parentResourceId": "bseT2"}
        )
        assert resp.json() == {"resourceId": "tblT2", "resourceType": "table"}

        # space with no space-visit -> empty 200
        resp = await client.get(
            "/api/user/last-visit",
            params={"resourceType": "space", "parentResourceId": "spcT2"},
        )
        assert resp.status_code == 200
        assert resp.content == b""

        # unknown parent -> empty 200
        resp = await client.get(
            "/api/user/last-visit/base-node", params={"parentResourceId": "bseNone"}
        )
        assert resp.status_code == 200
        assert resp.content == b""

        resp = await client.get(
            "/api/user/last-visit",
            params={"resourceType": "banana", "parentResourceId": "x"},
        )
        assert resp.status_code == 400
        assert resp.json()["message"].startswith(
            'Validation error: Invalid option: expected one of "space"|"Space"|'
        )

        resp = await client.get(
            "/api/user/last-visit",
            params={"resourceType": "Space", "parentResourceId": "x"},
        )
        assert resp.status_code == 400
        assert resp.json()["data"]["localization"]["i18nKey"] == (
            "httpErrors.lastVisit.invalidResourceType"
        )

        resp = await client.get(
            "/api/user/last-visit",
            params={"resourceType": "workflow", "parentResourceId": "x"},
        )
        assert resp.status_code == 500
    finally:
        await db.execute("DELETE FROM user_last_visit WHERE user_id=$1", user_id)
        await db.execute("DELETE FROM view WHERE id IN ('viwT2a','viwT2b')")
        await db.execute("DELETE FROM table_meta WHERE id='tblT2'")
        await db.execute("DELETE FROM collaborator WHERE id='collabT2'")
        await db.execute("DELETE FROM base WHERE id='bseT2'")
        await db.execute("DELETE FROM space WHERE id='spcT2'")


async def test_user_endpoints_require_auth(client):
    for method, path, body in (
        ("PATCH", "/api/user/name", {"name": "x"}),
        ("PATCH", "/api/user/lang", {"lang": "en"}),
        ("PATCH", "/api/user/notify-meta", {}),
        ("POST", "/api/user/track", {"event": "app.view"}),
        (
            "POST",
            "/api/user/last-visit",
            {"resourceType": "base", "resourceId": "b", "parentResourceId": "s"},
        ),
    ):
        resp = await client.request(method, path, json=body)
        assert resp.status_code == 401, path
        assert resp.json() == {"message": "Unauthorized", "status": 401, "code": "unauthorized"}
    resp = await client.get(
        "/api/user/last-visit", params={"resourceType": "space", "parentResourceId": "spcX"}
    )
    assert resp.status_code == 401


async def test_temp_token_can_access_user_endpoints(client):
    await _signed_in(client)
    token = (await client.get("/api/auth/temp-token")).json()["accessToken"]
    client.cookies.clear()
    resp = await client.patch(
        "/api/user/name",
        json={"name": "Via Token"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    me = await client.get("/api/auth/user/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["name"] == "Via Token"
