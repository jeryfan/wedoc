"""Adding a space/base collaborator notifies the added user (reference parity).

The reference emits a `collaboratorInvite` notification to each freshly added
user, surfaced via the notification list + unread-count. wedoc previously
added the collaborator without notifying.
"""

import httpx
from conftest import signup as _signup

from wedoc.main import create_app


async def _second_client():
    transport = httpx.ASGITransport(app=create_app(), raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_add_space_collaborator_notifies_invitee(client):
    # user A (fixture client) + user B (second in-process client, same dev DB)
    await _signup(client)
    space = (await client.post("/api/space", json={"name": "S"})).json()

    async with await _second_client() as client_b:
        await _signup(client_b)
        b_id = (await client_b.get("/api/auth/user")).json()["id"]

        add = await client.post(
            f"/api/space/{space['id']}/collaborator",
            json={
                "collaborators": [{"principalId": b_id, "principalType": "user"}],
                "role": "editor",
            },
        )
        assert add.status_code == 201, add.text

        count = (await client_b.get("/api/notifications/unread-count")).json()
        assert count == {"unreadCount": 1}

        listed = (await client_b.get("/api/notifications?notifyStates=unread")).json()
        items = listed["notifications"]
        assert len(items) == 1
        item = items[0]
        assert item["notifyType"] == "collaboratorInvite"
        assert item["isRead"] is False
        assert item["severity"] == "info"
        assert item["url"] == f"/space/{space['id']}"
        assert '"i18nKey":"email.templates.notify.collaboratorInvite.space"' in item["messageI18n"]
        assert "invited you to join the space S" in item["message"]
        assert item["notifyIcon"]["userId"]  # user-form icon (the inviter)
