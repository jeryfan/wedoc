"""Field creation must append the new field to every view's columnMeta.

Ports view.service.initViewColumnMeta (invoked from field-creating.service):
a freshly created field lands in each view's columnMeta at (max order + 1). A
link field's auto-created symmetric field is the exception — it exists on the
foreign table but is deliberately left out of that table's view columnMeta,
matching the reference.
"""

from conftest import signup as _signup


async def _create(client, path, body):
    resp = await client.post(path, json=body)
    assert resp.status_code in (200, 201), (path, resp.status_code, resp.text)
    return resp.json()


async def _setup(client):
    await _signup(client)
    space = await _create(client, "/api/space", {"name": "S"})
    base = await _create(client, "/api/base", {"spaceId": space["id"], "name": "A"})
    table = await _create(client, f"/api/base/{base['id']}/table", {"name": "T1"})
    return base["id"], table["id"]


async def _grid_view(client, table_id):
    views = (await client.get(f"/api/table/{table_id}/view")).json()
    return next(v for v in views if v["type"] == "grid")


async def test_default_table_grid_view_column_meta_is_dense_from_zero(client):
    _, tid = await _setup(client)
    fields = (await client.get(f"/api/table/{tid}/field")).json()
    column_meta = (await _grid_view(client, tid))["columnMeta"]
    assert set(column_meta) == {f["id"] for f in fields}
    assert sorted(m["order"] for m in column_meta.values()) == list(range(len(fields)))


async def test_created_field_is_appended_to_view_column_meta(client):
    _, tid = await _setup(client)
    before = (await _grid_view(client, tid))["columnMeta"]
    prev_max = max(m["order"] for m in before.values())

    created = await _create(client, f"/api/table/{tid}/field", {"type": "singleLineText"})

    after = (await _grid_view(client, tid))["columnMeta"]
    assert created["id"] in after
    assert after[created["id"]] == {"order": prev_max + 1}
    assert set(after) == set(before) | {created["id"]}


async def test_link_field_in_own_view_but_symmetric_field_not_in_foreign_view(client):
    # The reference adds the link field itself to its own table's columnMeta,
    # but the auto-created symmetric field is a real field on the foreign table
    # WITHOUT any entry in that table's view columnMeta.
    base_id, tid = await _setup(client)
    other = await _create(client, f"/api/base/{base_id}/table", {"name": "T2"})
    other_id = other["id"]
    foreign_before = (await _grid_view(client, other_id))["columnMeta"]

    link = await _create(
        client,
        f"/api/table/{tid}/field",
        {
            "type": "link",
            "options": {"relationship": "manyOne", "foreignTableId": other_id},
        },
    )

    own = (await _grid_view(client, tid))["columnMeta"]
    assert link["id"] in own

    sym_id = link["options"]["symmetricFieldId"]
    foreign_fields = (await client.get(f"/api/table/{other_id}/field")).json()
    assert sym_id in {f["id"] for f in foreign_fields}

    foreign_after = (await _grid_view(client, other_id))["columnMeta"]
    assert sym_id not in foreign_after
    assert set(foreign_after) == set(foreign_before)


async def test_duplicated_field_lands_after_source_in_column_meta(client):
    # The reference inserts a duplicated field midway between its source and the
    # next column in the originating view (source order 1, next 2 -> 1.5).
    _, tid = await _setup(client)
    grid = await _grid_view(client, tid)
    vid = grid["id"]
    fields = (await client.get(f"/api/table/{tid}/field")).json()
    count = next(f for f in fields if f["name"] == "Count")
    assert grid["columnMeta"][count["id"]]["order"] == 1

    dup = await _create(
        client,
        f"/api/table/{tid}/field/{count['id']}/duplicate",
        {"name": "Count copy", "viewId": vid},
    )

    after = (await _grid_view(client, tid))["columnMeta"]
    assert after[dup["id"]] == {"order": 1.5}
    assert sorted(m["order"] for m in after.values()) == [0, 1, 1.5, 2]
