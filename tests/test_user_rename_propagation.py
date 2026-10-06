"""USER_RENAME propagation: denormalized user-cell titles are patched on rename.

Sets up meta rows plus a real physical data table with single- and multi-value
``user`` columns, runs ``UserService.update_user_name``, and asserts the stored
snapshot titles are rewritten for the renamed user only, and only within bases
the user can access.
"""

import json
import os

os.environ.setdefault("PUBLIC_ORIGIN", "http://localhost:3000")
os.environ.setdefault("STORAGE_PREFIX", "http://localhost:3000")
os.environ.setdefault("BACKEND_CACHE_REDIS_URI", "redis://default:wedoc@127.0.0.1:46380")
os.environ.setdefault("PRISMA_DATABASE_URL", "postgresql://wedoc:wedoc@127.0.0.1:42346/wedoc")
os.environ.setdefault("SECRET_KEY", "refSecretKey000000")

from wedoc.db.provider import (
    add_field_column_sql,
    create_data_table_sql,
    create_schema_sql,
    drop_schema_sql,
)
from wedoc.modules.user import repository as user_repo
from wedoc.modules.user.service import UserService

# `client` / `db` fixtures are auto-registered from conftest.py


def _sfx() -> str:
    return os.urandom(5).hex()


async def _mk_space(db, user_id, *, member: bool):
    space_id = f"spc{_sfx()}"
    await db.execute(
        "INSERT INTO space (id, name, created_by) VALUES ($1,$2,$3)",
        space_id, "S", user_id,
    )
    if member:
        await db.execute(
            "INSERT INTO collaborator (id, role_name, resource_type, resource_id, "
            "principal_id, principal_type, created_by) VALUES ($1,$2,$3,$4,$5,$6,$7)",
            f"clb{_sfx()}", "owner", "space", space_id, user_id, "user", user_id,
        )
    return space_id


async def _mk_base_with_table(db, user_id, space_id, columns):
    """Create base + table_meta + one user field per column, plus the physical
    data table. ``columns`` maps db_field_name -> is_multiple_cell_value."""
    base_id = f"bse{_sfx()}"
    table_id = f"tbl{_sfx()}"
    await db.execute(
        'INSERT INTO base (id, space_id, name, "order", created_by) VALUES ($1,$2,$3,$4,$5)',
        base_id, space_id, "B", 1.0, user_id,
    )
    await db.execute(
        "INSERT INTO table_meta (id, base_id, name, db_table_name, version, "
        '"order", created_by) VALUES ($1,$2,$3,$4,$5,$6,$7)',
        table_id, base_id, "T", f"{base_id}.{table_id}", 1, 1.0, user_id,
    )
    field_ids = {}
    for dbname, multi in columns.items():
        fid = f"fld{_sfx()}"
        field_ids[dbname] = fid
        await db.execute(
            "INSERT INTO field (id, name, type, cell_value_type, db_field_type, "
            'db_field_name, table_id, "order", version, created_by, '
            "is_multiple_cell_value) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
            fid, dbname, "user", "string", "JSON" if multi else "TEXT",
            dbname, table_id, 1.0, 1, user_id, multi,
        )
    for stmt in create_schema_sql(base_id):
        await db.execute(stmt)
    for stmt in create_data_table_sql(base_id, table_id):
        await db.execute(stmt)
    for dbname, multi in columns.items():
        await db.execute(add_field_column_sql(base_id, table_id, dbname, "user", multi))
    return base_id, table_id, field_ids


async def test_user_rename_propagates_to_user_cells(client, db):
    user_id = f"usr{_sfx()}"
    other_id = f"usr{_sfx()}"
    await db.execute(
        "INSERT INTO users (id, name, email) VALUES ($1,$2,$3)",
        user_id, "Old Name", f"{user_id}@example.com",
    )

    space_id = await _mk_space(db, user_id, member=True)
    base_id, table_id, fids = await _mk_base_with_table(
        db, user_id, space_id, {"usr_single": False, "usr_multi": True}
    )
    # a base in a space the user is NOT a collaborator of must stay out of scope
    foreign_space = await _mk_space(db, user_id, member=False)
    foreign_base, _, foreign_fids = await _mk_base_with_table(
        db, user_id, foreign_space, {"usr_single": False}
    )

    async def _insert(rid, single, multi):
        await db.execute(
            f'INSERT INTO "{base_id}"."{table_id}" '
            '("__id","__created_by","__version","usr_single","usr_multi") '
            "VALUES ($1,$2,$3,$4,$5::jsonb)",
            rid, user_id, 1,
            None if single is None else json.dumps(single),
            None if multi is None else json.dumps(multi),
        )

    await _insert(
        "recA",
        {"id": user_id, "title": "Old Name", "email": "e"},
        [{"id": user_id, "title": "Old Name"}, {"id": other_id, "title": "Someone"}],
    )
    await _insert(
        "recB",
        {"id": other_id, "title": "Someone"},
        [{"id": other_id, "title": "Someone"}],
    )
    await _insert("recC", None, None)

    try:
        listed = await user_repo.list_user_snapshot_fields(user_id)
        listed_ids = {f["id"] for f in listed}
        assert listed_ids == {fids["usr_single"], fids["usr_multi"]}
        assert foreign_fids["usr_single"] not in listed_ids

        await UserService().update_user_name(user_id, "New Name")

        rows = await db.fetch(
            f'SELECT "__id","usr_single","usr_multi" FROM "{base_id}"."{table_id}"'
        )
        by_id = {r["__id"]: r for r in rows}
        a_single = json.loads(by_id["recA"]["usr_single"])
        assert a_single["title"] == "New Name"
        assert a_single["email"] == "e"
        a_multi = json.loads(by_id["recA"]["usr_multi"])
        assert a_multi[0] == {"id": user_id, "title": "New Name"}
        assert a_multi[1] == {"id": other_id, "title": "Someone"}
        b_single = json.loads(by_id["recB"]["usr_single"])
        assert b_single["title"] == "Someone"
        b_multi = json.loads(by_id["recB"]["usr_multi"])
        assert b_multi == [{"id": other_id, "title": "Someone"}]
        assert by_id["recC"]["usr_single"] is None
        assert by_id["recC"]["usr_multi"] is None
    finally:
        await db.execute(drop_schema_sql(base_id))
        await db.execute(drop_schema_sql(foreign_base))
