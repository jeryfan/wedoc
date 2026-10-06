"""Organization stub endpoints parity (community edition).

Organization/department data is enterprise-only, so the community build returns
stubs: /me is an empty 200, /department-user is {users:[], total:0}, /department
is []. Query params are ignored.
"""

from conftest import signup as _signup


async def test_organization_stubs(client):
    await _signup(client)

    me = await client.get("/api/organization/me")
    assert me.status_code == 200 and me.content == b"", me.text

    du = await client.get("/api/organization/department-user", params={"search": "x"})
    assert du.status_code == 200, du.text
    assert du.json() == {"users": [], "total": 0}

    dept = await client.get("/api/organization/department", params={"parentId": "x"})
    assert dept.status_code == 200, dept.text
    assert dept.json() == []
