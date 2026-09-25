"""Read/write side of the instance `setting` table (SettingModel port)."""

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from ...core import cls
from ...db import engine as db_engine
from ...db.models_meta import Setting


def parse_setting_content(content: str | None) -> Any:
    if not content:
        return None
    try:
        return json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return content


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


async def get_setting(names: list[str] | None = None) -> dict[str, Any]:
    async with db_engine.session() as session:
        rows = (await session.execute(select(Setting.name, Setting.content))).all()
    result: dict[str, Any] = {"instanceId": ""}
    wanted = set(names) if names is not None else None
    for name, content in rows:
        if wanted is not None and name not in wanted:
            continue
        result[name] = parse_setting_content(content)
    return result


async def get_setting_rows(names: list[str] | None = None) -> list[dict[str, Any]]:
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(Setting.name, Setting.content, Setting.created_time)
            )
        ).all()
    wanted = set(names) if names is not None else None
    out: list[dict[str, Any]] = []
    for name, content, created_time in rows:
        if wanted is not None and name not in wanted:
            continue
        out.append(
            {
                "name": name,
                "content": parse_setting_content(content),
                "createdTime": _iso(created_time),
            }
        )
    return out


async def upsert_setting(name: str, value: Any) -> Any:
    user_id = cls.get("user.id")
    content = json.dumps(value if value is not None else None)
    async with db_engine.session() as session:
        existing = (
            await session.execute(select(Setting.name).where(Setting.name == name))
        ).first()
        if existing is None:
            await session.execute(
                Setting.__table__.insert().values(
                    name=name, content=content, created_by=user_id
                )
            )
        else:
            await session.execute(
                Setting.__table__.update()
                .where(Setting.name == name)
                .values(content=content, last_modified_by=user_id)
            )
        await session.commit()
        row = (
            await session.execute(select(Setting.content).where(Setting.name == name))
        ).first()
    return parse_setting_content(row[0]) if row else None
