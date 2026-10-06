"""Field delete -> table_trash capture -> restore-field flow.

Sets up a space (owner collaborator) + base + table with a primary and a
non-primary field plus a physical data table, then drives FieldService and
TrashService directly under a signed-in cls context: delete the non-primary
field, assert a ``field`` trash item surfaces (with a resourceMap entry),
restore it via the stream service, and assert the field is live again and the
trash row is gone.
"""

import json
import os

os.environ.setdefault("PUBLIC_ORIGIN", "http://localhost:3000")
os.environ.setdefault("STORAGE_PREFIX", "http://localhost:3000")
os.environ.setdefault("BACKEND_CACHE_REDIS_URI", "redis://default:wedoc@127.0.0.1:46380")
os.environ.setdefault("PRISMA_DATABASE_URL", "postgresql://wedoc:wedoc@127.0.0.1:42346/wedoc")
os.environ.setdefault("SECRET_KEY", "refSecretKey000000")

import pytest

from wedoc.core import cls
from wedoc.core.errors import ApiError
from wedoc.db.provider import (
    add_field_column_sql,
    create_data_table_sql,
    create_schema_sql,
    drop_schema_sql,
)
from wedoc.modules.field.service import FieldService
from wedoc.modules.trash import repository as trash_repo
from wedoc.modules.trash.service import TrashService

# `client` / `db` fixtures are auto-registered from conftest.py


def _sfx() -> str:
    return os.urandom(5).hex()


async def _insert_field(db, table_id, user_id, fid, name, db_name, order, *, primary=False):
    await db.execute(
        "INSERT INTO field (id, name, type, cell_value_type, db_field_type, "
        'db_field_name, table_id, "order", version, created_by, options, is_primary) '
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)",
        fid, name, "singleLineText", "string", "TEXT", db_name, table_id, order, 1,
        user_id, "{}", True if primary else None,
    )


async def _setup(db):
    user_id = f"usr{_sfx()}"
    await db.execute(
        "INSERT INTO users (id, name, email) VALUES ($1,$2,$3)",
        user_id, "Owner", f"{user_id}@example.com",
    )
    space_id = f"spc{_sfx()}"
    await db.execute(
        "INSERT INTO space (id, name, created_by) VALUES ($1,$2,$3)", space_id, "S", user_id
    )
    await db.execute(
        "INSERT INTO collaborator (id, role_name, resource_type, resource_id, "
        "principal_id, principal_type, created_by) VALUES ($1,$2,$3,$4,$5,$6,$7)",
        f"clb{_sfx()}", "owner", "space", space_id, user_id, "user", user_id,
    )
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
    primary_id = f"fld{_sfx()}"
    field_id = f"fld{_sfx()}"
    await _insert_field(db, table_id, user_id, primary_id, "Name", "name", 1.0, primary=True)
    await _insert_field(db, table_id, user_id, field_id, "Notes", "notes", 2.0)
    for stmt in create_schema_sql(base_id):
        await db.execute(stmt)
    for stmt in create_data_table_sql(base_id, table_id):
        await db.execute(stmt)
    await db.execute(add_field_column_sql(base_id, table_id, "name", "singleLineText"))
    await db.execute(add_field_column_sql(base_id, table_id, "notes", "singleLineText"))
    for _ in range(2):
        await db.execute(
            f'INSERT INTO "{base_id}"."{table_id}" ("__id","__created_by","__version") '
            "VALUES ($1,$2,$3)",
            f"rec{_sfx()}", user_id, 1,
        )
    return user_id, base_id, table_id, primary_id, field_id


async def test_field_delete_trash_and_restore(client, db):
    user_id, base_id, table_id, primary_id, field_id = await _setup(db)
    token = cls.enter({"user": {"id": user_id}})
    try:
        fields = FieldService()
        trash = TrashService()

        await fields.delete_field(table_id, field_id)

        live_ids = {f["id"] for f in await fields.list_fields(table_id)}
        assert field_id not in live_ids
        assert primary_id in live_ids

        rows = await trash_repo.list_table_trash(table_id, None, 50, None, None, None, None)
        field_rows = [r for r in rows if r["resourceType"] == "field"]
        assert len(field_rows) == 1
        snapshot = json.loads(field_rows[0]["snapshot"])
        assert [f["id"] for f in snapshot["fields"]] == [field_id]
        trash_id = field_rows[0]["id"]

        items = await trash.get_trash_items(table_id, "table", None, 20, None, None, None, None)
        item = next(i for i in items["trashItems"] if i["id"] == trash_id)
        assert item["resourceType"] == "field"
        assert field_id in item["resourceIds"]
        assert items["resourceMap"][field_id]["name"] == "Notes"

        events = await trash.restore_field_trash_stream(trash_id, table_id)
        assert events == [{"id": "done", "totalCount": 2, "updatedCount": 0}]

        live_ids = {f["id"] for f in await fields.list_fields(table_id)}
        assert field_id in live_ids
        assert await trash_repo.find_table_trash(trash_id, table_id) is None

        with pytest.raises(ApiError):
            await fields.delete_field(table_id, primary_id)
    finally:
        cls.exit(token)
        await db.execute(drop_schema_sql(base_id))
