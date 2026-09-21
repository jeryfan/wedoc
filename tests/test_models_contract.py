"""Contract test: ORM models vs the migrated PostgreSQL schema.

Compares every mapped table/column (names + nullability) in
`wedoc.db.models_meta` and `wedoc.db.models_data` against
information_schema of the development database. The expected table lists
are the @@map names extracted from the two upstream prisma schemas
(`_prisma_migrations` excluded; `__undo_log` is a migration-managed
internal table).
"""

import os
from collections import defaultdict

import asyncpg
import pytest

from wedoc.db import models_data, models_meta

DSN = os.environ.get("WEDOC_TEST_DATABASE_DSN", "postgresql://wedoc:wedoc@127.0.0.1:42346/wedoc")

META_TABLES = [
    "space",
    "data_db_connection",
    "space_data_db_binding",
    "space_data_db_migration_job",
    "base_data_db_move_job",
    "pin_resource",
    "base",
    "table_meta",
    "table_query_observation_shard",
    "schema_operation",
    "field",
    "computed_update_outbox",
    "computed_update_outbox_seed",
    "computed_update_stage_ledger",
    "computed_update_dead_letter",
    "computed_update_run_history",
    "computed_update_pause_scope",
    "computed_field_activity",
    "computed_table_activity",
    "computed_task_field_ref",
    "view",
    "ops",
    "reference",
    "users",
    "account",
    "attachments",
    "attachments_table",
    "collaborator",
    "invitation",
    "invitation_record",
    "notification",
    "access_token",
    "setting",
    "oauth_app",
    "oauth_app_authorized",
    "oauth_app_secret",
    "oauth_app_token",
    "record_history",
    "trash",
    "table_trash",
    "record_trash",
    "record_removal_tombstone",
    "plugin",
    "plugin_install",
    "dashboard",
    "comment",
    "comment_subscription",
    "integration",
    "plugin_panel",
    "plugin_context_menu",
    "user_last_visit",
    "template",
    "template_category",
    "task",
    "task_run",
    "task_reference",
    "waitlist",
    "base_node",
    "base_node_folder",
    "base_share",
    "short_link",
]

DATA_TABLES = [
    "computed_update_outbox",
    "computed_update_outbox_seed",
    "computed_update_stage_ledger",
    "computed_update_dead_letter",
    "computed_update_run_history",
    "computed_update_pause_scope",
    "computed_field_activity",
    "computed_table_activity",
    "computed_task_field_ref",
    "record_history",
    "table_trash",
    "record_trash",
    "record_removal_tombstone",
    "attachments",
    "attachments_table",
]

EXPECTED_DB_TABLES = sorted({*META_TABLES, "__undo_log", "_prisma_migrations"})


async def fetch_db_schema() -> dict[str, dict[str, bool]]:
    conn = await asyncpg.connect(DSN)
    try:
        rows = await conn.fetch(
            "SELECT table_name, column_name, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_schema = 'public' "
            "ORDER BY table_name, ordinal_position"
        )
    finally:
        await conn.close()
    schema: dict[str, dict[str, bool]] = defaultdict(dict)
    for row in rows:
        schema[row["table_name"]][row["column_name"]] = row["is_nullable"] == "YES"
    return dict(schema)


def model_schema(base) -> dict[str, dict[str, bool]]:
    return {
        table.name: {col.name: col.nullable for col in table.columns}
        for table in base.metadata.tables.values()
    }


def diff_schema(model: dict[str, dict[str, bool]], db: dict[str, dict[str, bool]]) -> list[str]:
    errors: list[str] = []
    for table in sorted(set(model) | set(db)):
        if table not in model:
            errors.append(f"{table}: missing in model")
            continue
        if table not in db:
            errors.append(f"{table}: missing in database")
            continue
        m_cols, d_cols = model[table], db[table]
        for col in sorted(set(m_cols) - set(d_cols)):
            errors.append(f"{table}.{col}: missing in database")
        for col in sorted(set(d_cols) - set(m_cols)):
            errors.append(f"{table}.{col}: missing in model")
        for col in sorted(set(m_cols) & set(d_cols)):
            if m_cols[col] != d_cols[col]:
                errors.append(f"{table}.{col}: nullable model={m_cols[col]} db={d_cols[col]}")
    return errors


@pytest.fixture(scope="module")
async def db_schema() -> dict[str, dict[str, bool]]:
    return await fetch_db_schema()


async def test_db_table_inventory(db_schema):
    assert sorted(db_schema) == EXPECTED_DB_TABLES


async def test_meta_models_cover_expected_tables():
    assert sorted(model_schema(models_meta.MetaBase)) == sorted([*META_TABLES, "__undo_log"])


async def test_data_models_cover_expected_tables():
    assert sorted(model_schema(models_data.DataBase)) == sorted(DATA_TABLES)


async def test_meta_columns_and_nullable_match_db(db_schema):
    expected = {*META_TABLES, "__undo_log"}
    db_subset = {t: cols for t, cols in db_schema.items() if t in expected}
    errors = diff_schema(model_schema(models_meta.MetaBase), db_subset)
    assert errors == []


async def test_data_columns_and_nullable_match_db(db_schema):
    db_subset = {t: cols for t, cols in db_schema.items() if t in DATA_TABLES}
    errors = diff_schema(model_schema(models_data.DataBase), db_subset)
    assert errors == []
