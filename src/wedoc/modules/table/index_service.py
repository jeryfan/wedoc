"""Table full-text search index management — ports table-index.service.ts.

Single-PG port: search indexes are real btree indexes named `idx_trgm_*` on the
per-base physical table (schema = base_id, table = table_id). activated-index
checks their existence; toggle creates/drops them; abnormal-index compares the
expected set (one per non-button field) against what exists. search-vector-status
returns the read-only `disabled` shape (the reference's generated tsvector column
is a v2-only feature, disabled on every base in the single-PG deployment — matches
the reference which reports `disabled` regardless of the pg_trgm index state).
"""

from typing import Any

from sqlalchemy import text

from ...core.errors import ApiError, HttpErrorCode
from ...db import engine as db_engine
from ..field import repository as field_repository
from . import repository

_PG_MAX_INDEX_LEN = 63
_DELIMITER_LEN = 3
_INDEX_PREFIX = "idx_trgm"


def _not_supported_index_type() -> ApiError:
    return ApiError(
        "Table index type not supported",
        HttpErrorCode.VALIDATION_ERROR,
        {"localization": {"i18nKey": "httpErrors.table.notSupportTableIndex"}},
    )


def _index_name(table: str, db_field_name: str, field_id: str) -> str:
    max_table_len = _PG_MAX_INDEX_LEN - len(field_id) - len(_INDEX_PREFIX) - _DELIMITER_LEN
    table_len = min(max_table_len, len(table))
    field_len = (
        0
        if max_table_len < len(table)
        else _PG_MAX_INDEX_LEN
        - len(field_id)
        - len(_INDEX_PREFIX)
        - table_len
        - _DELIMITER_LEN
    )
    abb_field = db_field_name[:field_len]
    return f"{_INDEX_PREFIX}_{table[:table_len]}_{abb_field}_{field_id}"


class TableIndexService:
    async def _table(self, table_id: str) -> dict[str, Any]:
        table = await field_repository.get_table_meta_by_id(table_id)
        if table is None:
            raise ApiError(
                f"Table {table_id} not found",
                HttpErrorCode.NOT_FOUND,
                {"localization": {"i18nKey": "httpErrors.table.notFound"}},
            )
        return table

    async def _search_fields(self, table_id: str) -> list[dict[str, Any]]:
        rows = await repository.list_field_rows(table_id)
        return [r for r in rows if r["type"] != "button"]

    async def get_activated_indexes(
        self, table_id: str, index_type: str = "search"
    ) -> list[str]:
        table = await self._table(table_id)
        if index_type != "search":
            raise _not_supported_index_type()
        schema, tbl = table["base_id"], table["id"]
        sql = text(
            "SELECT EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname = :s "
            "AND tablename = :t AND indexname LIKE :p) AS exists"
        )
        async with db_engine.session() as session:
            result = await session.execute(
                sql, {"s": schema, "t": tbl, "p": f"{_INDEX_PREFIX}%"}
            )
            exists = bool(result.scalar())
        return ["search"] if exists else []

    async def toggle_index(self, table_id: str, ro: dict[str, Any]) -> None:
        index_type = ro.get("type")
        if index_type != "search":
            raise _not_supported_index_type()
        activated = await self.get_activated_indexes(table_id)
        if "search" in activated:
            await self._drop_indexes(table_id)
        else:
            await self._create_indexes(table_id)

    async def repair_index(self, table_id: str, index_type: str) -> None:
        if index_type != "search":
            raise _not_supported_index_type()
        await self._drop_indexes(table_id)
        await self._create_indexes(table_id)

    async def get_search_vector_status(self, table_id: str) -> dict[str, Any]:
        await self._table(table_id)
        return {
            "tableId": table_id,
            "state": "disabled",
            "configured": False,
            "active": False,
            "coveredFieldCount": 0,
        }

    async def get_abnormal_index(self, table_id: str, index_type: str) -> list[dict[str, str]]:
        activated = await self.get_activated_indexes(table_id)
        if index_type not in activated:
            return []
        table = await self._table(table_id)
        schema, tbl = table["base_id"], table["id"]
        fields = await self._search_fields(table_id)
        expected = {_index_name(tbl, f["db_field_name"], f["id"]) for f in fields}
        existing = await self._existing_index_names(schema, tbl)
        lacking = expected - existing
        redundant = existing - expected
        diff = sorted(redundant | lacking)
        return [{"indexName": name} for name in diff]

    async def _existing_index_names(self, schema: str, tbl: str) -> set[str]:
        sql = text(
            "SELECT indexname::text FROM pg_indexes WHERE schemaname = :s "
            "AND tablename = :t AND indexname LIKE :p"
        )
        async with db_engine.session() as session:
            rows = await session.execute(
                sql, {"s": schema, "t": tbl, "p": f"{_INDEX_PREFIX}%"}
            )
            return {r[0] for r in rows}

    async def _create_indexes(self, table_id: str) -> None:
        table = await self._table(table_id)
        schema, tbl = table["base_id"], table["id"]
        fields = await self._search_fields(table_id)
        statements: list[str] = []
        for f in fields:
            name = _index_name(tbl, f["db_field_name"], f["id"])
            statements.append(
                f'CREATE INDEX IF NOT EXISTS "{name}" ON "{schema}"."{tbl}" '
                f'USING btree ("{f["db_field_name"]}")'
            )
        if statements:
            await repository.execute_data_ddl(statements)

    async def _drop_indexes(self, table_id: str) -> None:
        table = await self._table(table_id)
        schema, tbl = table["base_id"], table["id"]
        existing = await self._existing_index_names(schema, tbl)
        statements = [f'DROP INDEX IF EXISTS "{schema}"."{name}"' for name in existing]
        if statements:
            await repository.execute_data_ddl(statements)
