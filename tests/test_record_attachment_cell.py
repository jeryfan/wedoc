"""Attachment cell contract: writing ``[{token, name}]`` resolves each token to
the stored attachment metadata and a per-cell id, persisting
``{id, name, token, path, size, mimetype, width?, height?}`` (no signed urls);
reads decorate the cell with an ephemeral ``presignedUrl`` and, for images,
``smThumbnailUrl`` / ``lgThumbnailUrl`` (thumbnail urls are omitted for
non-images). Mirrors the reference typecast.validate castToAttachment (write) and
record.service getAttachmentPresignedCellValue (read).
"""

import base64
import io

from conftest import signup as _signup

# 1x1 png (height 1 -> no thumbnail cropped, image urls fall back to presigned).
_PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
_TXT = b"hello attachment world\n"

_STORED_KEYS = {"id", "name", "token", "path", "size", "mimetype", "width", "height"}


def _tall_png(width: int, height: int) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (12, 200, 50)).save(buffer, format="PNG")
    return buffer.getvalue()


async def _create(client, path, body):
    resp = await client.post(path, json=body)
    assert resp.status_code in (200, 201), (path, resp.status_code, resp.text)
    return resp.json()


async def _setup(client):
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    base = await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"})
    table = await _create(client, f"/api/base/{base['id']}/table", {"name": "T1"})
    field = await _create(
        client, f"/api/table/{table['id']}/field", {"type": "attachment", "name": "Files"}
    )
    return base["id"], table["id"], field["id"]


async def _upload(client, base_id, data, content_type):
    sig = await _create(
        client,
        "/api/attachments/signature",
        {"contentType": content_type, "contentLength": len(data), "type": 1, "baseId": base_id},
    )
    put = await client.put(sig["url"], content=data, headers={"content-type": content_type})
    assert put.status_code == 200, put.text
    notify = await client.post(f"/api/attachments/notify/{sig['token']}")
    assert notify.status_code == 201, notify.text
    return sig["token"]


async def _create_record(client, tid, field_id, cell):
    created = await _create(
        client,
        f"/api/table/{tid}/record",
        {"fieldKeyType": "id", "records": [{"fields": {field_id: cell}}]},
    )
    return created["records"][0]


async def _get_cell(client, tid, rid, field_id):
    got = (await client.get(f"/api/table/{tid}/record/{rid}?fieldKeyType=id")).json()
    return got["fields"].get(field_id)


async def test_image_cell_resolves_and_read_exposes_urls(client):
    base_id, tid, field_id = await _setup(client)
    token = await _upload(client, base_id, _PNG_1x1, "image/png")

    rec = await _create_record(client, tid, field_id, [{"token": token, "name": "1x1.png"}])
    # write echo carries the resolved-but-unsigned cell
    echo_item = rec["fields"][field_id][0]
    assert _STORED_KEYS <= set(echo_item), echo_item
    assert echo_item["token"] == token
    assert echo_item["name"] == "1x1.png"
    assert echo_item["id"].startswith("act")
    assert echo_item["size"] == len(_PNG_1x1)
    assert echo_item["mimetype"] == "image/png"
    assert echo_item["width"] == 1 and echo_item["height"] == 1
    assert echo_item["path"] == f"table/{token}"
    assert "presignedUrl" not in echo_item

    item = (await _get_cell(client, tid, rec["id"], field_id))[0]
    assert _STORED_KEYS <= set(item)
    assert item["id"] == echo_item["id"]
    for key in ("presignedUrl", "smThumbnailUrl", "lgThumbnailUrl"):
        assert isinstance(item.get(key), str) and item[key], (key, item)


async def test_tall_image_generates_real_sm_thumbnail(client):
    base_id, tid, field_id = await _setup(client)
    token = await _upload(client, base_id, _tall_png(10, 100), "image/png")
    rec = await _create_record(client, tid, field_id, [{"token": token, "name": "tall.png"}])
    item = (await _get_cell(client, tid, rec["id"], field_id))[0]
    # height 100 > 56 -> a real sm thumbnail is cropped (distinct url);
    # 100 < 525 -> lg falls back to the presigned original.
    assert item["smThumbnailUrl"] != item["presignedUrl"]
    assert item["lgThumbnailUrl"] == item["presignedUrl"]


async def test_non_image_cell_omits_thumbnail_urls(client):
    base_id, tid, field_id = await _setup(client)
    token = await _upload(client, base_id, _TXT, "text/plain")
    rec = await _create_record(client, tid, field_id, [{"token": token, "name": "note.txt"}])
    item = (await _get_cell(client, tid, rec["id"], field_id))[0]
    assert isinstance(item.get("presignedUrl"), str) and item["presignedUrl"]
    assert "smThumbnailUrl" not in item
    assert "lgThumbnailUrl" not in item
    assert "width" not in item and "height" not in item


async def test_id_stable_on_readback_rewrite_and_new_on_bare(client):
    base_id, tid, field_id = await _setup(client)
    token = await _upload(client, base_id, _PNG_1x1, "image/png")
    rec = await _create_record(client, tid, field_id, [{"token": token, "name": "1x1.png"}])
    rid = rec["id"]
    original_id = rec["fields"][field_id][0]["id"]

    read_cell = await _get_cell(client, tid, rid, field_id)
    resp = await client.patch(
        f"/api/table/{tid}/record/{rid}",
        json={"fieldKeyType": "id", "record": {"fields": {field_id: read_cell}}},
    )
    assert resp.status_code == 200, resp.text
    after = (await _get_cell(client, tid, rid, field_id))[0]
    assert after["id"] == original_id  # id preserved when the item carries one

    bare_cell = [{"token": token, "name": "x"}]
    resp = await client.patch(
        f"/api/table/{tid}/record/{rid}",
        json={"fieldKeyType": "id", "record": {"fields": {field_id: bare_cell}}},
    )
    assert resp.status_code == 200, resp.text
    bare = (await _get_cell(client, tid, rid, field_id))[0]
    assert bare["id"] != original_id  # a bare re-write mints a fresh id


async def test_client_supplied_id_preserved(client):
    base_id, tid, field_id = await _setup(client)
    token = await _upload(client, base_id, _PNG_1x1, "image/png")
    rec = await _create_record(
        client, tid, field_id, [{"token": token, "name": "x.png", "id": "actCLIENTGIVEN01"}]
    )
    assert rec["fields"][field_id][0]["id"] == "actCLIENTGIVEN01"


async def test_unresolved_token_errors(client):
    _base_id, tid, field_id = await _setup(client)
    records = [{"fields": {field_id: [{"token": "nope", "name": "x"}]}}]
    resp = await client.post(
        f"/api/table/{tid}/record",
        json={"fieldKeyType": "id", "records": records},
    )
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["message"] == "Attachment(nope) not found"
    assert body["data"]["domainCode"] == "validation.field.attachment_not_found"


async def test_multi_attachment_mixed(client):
    base_id, tid, field_id = await _setup(client)
    img = await _upload(client, base_id, _PNG_1x1, "image/png")
    txt = await _upload(client, base_id, _TXT, "text/plain")
    rec = await _create_record(
        client,
        tid,
        field_id,
        [{"token": img, "name": "a.png"}, {"token": txt, "name": "b.txt"}],
    )
    cell = await _get_cell(client, tid, rec["id"], field_id)
    assert len(cell) == 2
    image_item, text_item = cell
    assert {"smThumbnailUrl", "lgThumbnailUrl"} <= set(image_item)
    assert "smThumbnailUrl" not in text_item and "lgThumbnailUrl" not in text_item
    assert image_item["id"] != text_item["id"]


async def test_dedicated_upload_endpoint_matches_generic_shape(client):
    _base_id, tid, field_id = await _setup(client)
    created = await _create(
        client, f"/api/table/{tid}/record", {"fieldKeyType": "id", "records": [{"fields": {}}]}
    )
    rid = created["records"][0]["id"]
    resp = await client.post(
        f"/api/table/{tid}/record/{rid}/{field_id}/uploadAttachment",
        files={"file": ("dedicated.png", _PNG_1x1, "image/png")},
    )
    assert resp.status_code == 201, resp.text
    item = (await _get_cell(client, tid, rid, field_id))[0]
    # the dedicated endpoint funnels through the same write resolution + read
    # enrichment as a generic record write.
    assert _STORED_KEYS <= set(item), item
    assert item["name"] == "dedicated.png"
    assert item["mimetype"] == "image/png"
    assert item["id"].startswith("act")
    for key in ("presignedUrl", "smThumbnailUrl", "lgThumbnailUrl"):
        assert isinstance(item.get(key), str) and item[key], (key, item)
