"""uploadAttachment echo carries the ephemeral presignedUrl (like reads do),
matching the reference's mutation output for attachment cells.
"""

from conftest import signup as _signup


async def _create(client, path, body):
    resp = await client.post(path, json=body)
    assert resp.status_code in (200, 201), (path, resp.status_code, resp.text)
    return resp.json()


async def test_upload_attachment_echo_has_presigned_url(client):
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    base = await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"})
    tid = (await _create(client, f"/api/base/{base['id']}/table", {"name": "T"}))["id"]
    fid = (await _create(
        client, f"/api/table/{tid}/field", {"name": "Att", "type": "attachment"}
    ))["id"]
    rid = (await _create(client, f"/api/table/{tid}/record",
        {"fieldKeyType": "name", "records": [{"fields": {"Name": "r1"}}]}))["records"][0]["id"]

    resp = await client.post(
        f"/api/table/{tid}/record/{rid}/{fid}/uploadAttachment",
        files={"file": ("note.txt", b"hello attachment", "text/plain")},
    )
    assert resp.status_code == 201, resp.text
    cell = resp.json()["fields"][fid]
    assert isinstance(cell, list) and len(cell) == 1, cell
    item = cell[0]
    assert item["name"] == "note.txt"
    assert item["size"] == len(b"hello attachment")
    assert item["mimetype"] == "text/plain"
    # the mutation echo signs the cell like a read does
    assert item.get("presignedUrl")
