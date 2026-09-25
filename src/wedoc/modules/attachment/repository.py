"""Attachment persistence — attachments meta table."""

from typing import Any

from sqlalchemy import insert, select

from ...core.ids import cuid
from ...db import engine as db_engine
from ...db.models_meta import Attachments


async def create_attachment(
    token: str,
    hash_: str,
    size: int,
    mimetype: str,
    path: str,
    width: int | None,
    height: int | None,
    created_by: str,
) -> None:
    async with db_engine.session() as session:
        await session.execute(
            insert(Attachments).values(
                id=cuid(),
                token=token,
                hash=hash_,
                size=size,
                mimetype=mimetype,
                path=path,
                width=width,
                height=height,
                created_by=created_by,
            )
        )
        await session.commit()


async def find_attachment_by_token(token: str) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(Attachments).where(
                    Attachments.token == token, Attachments.deleted_time.is_(None)
                )
            )
        ).scalar_one_or_none()
    if row is None:
        return None
    return {"token": row.token, "mimetype": row.mimetype, "path": row.path}
