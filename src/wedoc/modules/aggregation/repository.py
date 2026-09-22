"""Raw query helpers for the aggregation module."""

from typing import Any

from sqlalchemy import text

from ...db import engine as db_engine


async def raw_rows(sql: str, bind: dict[str, Any]) -> list[dict[str, Any]]:
    async with db_engine.session() as session:
        rows = (await session.execute(text(sql), bind)).mappings().all()
    return [dict(r) for r in rows]
