"""share persistence — resolve a shared view by shareId."""

from typing import Any

from sqlalchemy import select

from ...db import engine as db_engine
from ...db.models_meta import View
from ..view.service import _view_vo


async def find_view_by_share_id(share_id: str) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(View).where(
                    View.share_id == share_id,
                    View.enable_share.is_(True),
                    View.deleted_time.is_(None),
                )
            )
        ).scalars().first()
    if row is None:
        return None
    return {c.name: getattr(row, c.name) for c in View.__table__.columns}


def view_vo(row: dict[str, Any]) -> dict[str, Any]:
    return _view_vo(row)
