"""Public setting parity.

get_public_setting exposes the enterprise fields the frontend reads
(githubAppConfigured / scrapeEnabled / connectorEventEnabled /
mobileAuthExchange / socialAuthProviders / emailCodeSigninEnabled /
buildVersion); the admin surface stays gated behind "User is not an admin".
"""

from conftest import signup as _signup


async def test_public_setting_enterprise_fields(client):
    resp = await client.get("/api/admin/setting/public")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["githubAppConfigured"] is False
    assert body["scrapeEnabled"] is False
    assert body["connectorEventEnabled"] is False
    assert body["mobileAuthExchange"] is True
    assert body["socialAuthProviders"] == []
    assert body["emailCodeSigninEnabled"] is False
    assert isinstance(body["buildVersion"], str)
    # controller-appended threshold fields remain present
    assert body["changeEmailSendCodeMailRate"] == 30
    assert body["resetPasswordSendMailRate"] == 30
    assert body["signupVerificationSendCodeMailRate"] == 30


async def test_admin_setting_requires_admin(client):
    await _signup(client)
    for method, path, payload in (
        ("get", "/api/admin/setting", None),
        ("patch", "/api/admin/setting", {"brandName": "x"}),
        ("get", "/api/admin/setting/test-public-access", None),
    ):
        resp = await getattr(client, method)(path, **({"json": payload} if payload else {}))
        assert resp.status_code == 403, (path, resp.text)
        assert resp.json()["message"] == "User is not an admin"
