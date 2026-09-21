"""Read-side of the instance `setting` table (SettingModel.getSetting port).

The write/admin surface lands with the setting module (M6); auth/user only
need the parsed name -> content map.
"""

import json
from typing import Any

from sqlalchemy import select

from ...db import engine as db_engine
from ...db.models_meta import Setting


def parse_setting_content(content: str | None) -> Any:
    if not content:
        return None
    try:
        return json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return content


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
