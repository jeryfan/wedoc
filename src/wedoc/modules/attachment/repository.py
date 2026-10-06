"""Attachment persistence — attachments meta table."""

import json
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
    thumbnail_path: str | None = None,
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
                thumbnail_path=thumbnail_path,
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


async def find_attachments_meta_by_tokens(tokens: list[str]) -> dict[str, dict[str, Any]]:
    """token -> {token, path, size, mimetype, width?, height?} for the write path.

    Mirrors the reference getAttachmentCvMapByCv select; width/height are omitted when
    NULL so they never surface as null in the persisted cell.
    """
    if not tokens:
        return {}
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(Attachments).where(
                    Attachments.token.in_(tokens), Attachments.deleted_time.is_(None)
                )
            )
        ).scalars()
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            meta: dict[str, Any] = {
                "token": row.token,
                "path": row.path,
                "size": int(row.size),
                "mimetype": row.mimetype,
            }
            if row.width is not None:
                meta["width"] = row.width
            if row.height is not None:
                meta["height"] = row.height
            result[row.token] = meta
    return result


async def find_thumbnail_paths_by_tokens(
    tokens: list[str],
) -> dict[str, dict[str, str]]:
    """token -> parsed {sm?, lg?} for rows carrying a thumbnail_path (read path)."""
    if not tokens:
        return {}
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(Attachments.token, Attachments.thumbnail_path).where(
                    Attachments.token.in_(tokens),
                    Attachments.thumbnail_path.is_not(None),
                    Attachments.deleted_time.is_(None),
                )
            )
        ).all()
    result: dict[str, dict[str, str]] = {}
    for token, thumbnail_path in rows:
        try:
            parsed = json.loads(thumbnail_path)
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, dict):
            result[token] = parsed
    return result
