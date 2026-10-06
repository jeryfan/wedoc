"""Short-link persistence — raw-row access for the short-link service."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ...core.ids import cuid, random_string
from ...db import engine as db_engine
from ...db.models_meta import BaseShare, ShortLink, Template, View

SHORT_LINK_CODE_LENGTH = 9
SHORT_LINK_CODE_MAX_RETRY = 5


async def view_share_exists(share_id: str) -> bool:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(View.id).where(
                    View.share_id == share_id,
                    View.enable_share.is_(True),
                    View.deleted_time.is_(None),
                )
            )
        ).first()
    return row is not None


async def base_share_exists(share_id: str) -> bool:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(BaseShare.id).where(
                    BaseShare.share_id == share_id, BaseShare.enabled.is_(True)
                )
            )
        ).first()
    return row is not None


async def base_share_target(share_id: str) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(BaseShare.base_id, BaseShare.node_id).where(
                    BaseShare.share_id == share_id, BaseShare.enabled.is_(True)
                )
            )
        ).first()
    return {"baseId": row[0], "nodeId": row[1]} if row else None


async def template_published(template_id: str) -> bool:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(Template.id).where(
                    Template.id == template_id, Template.is_published.is_(True)
                )
            )
        ).first()
    return row is not None


async def find_active(link_type: str, resource_id: str) -> str | None:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(ShortLink.code).where(
                    ShortLink.type == link_type,
                    ShortLink.resource_id == resource_id,
                    ShortLink.deleted_time.is_(None),
                )
            )
        ).first()
    return row[0] if row else None


async def find_by_code(code: str) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(ShortLink.code, ShortLink.type, ShortLink.resource_id).where(
                    ShortLink.code == code, ShortLink.deleted_time.is_(None)
                )
            )
        ).first()
    return {"code": row[0], "type": row[1], "resourceId": row[2]} if row else None


async def mark_deleted_by_resource(link_type: str, resource_id: str) -> None:
    async with db_engine.session() as session:
        await session.execute(
            ShortLink.__table__.update()
            .where(
                ShortLink.type == link_type,
                ShortLink.resource_id == resource_id,
                ShortLink.deleted_time.is_(None),
            )
            .values(deleted_time=datetime.now(UTC).replace(tzinfo=None))
        )
        await session.commit()


async def _reuse_existing(link_type: str, resource_id: str) -> str | None:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(ShortLink.code, ShortLink.deleted_time).where(
                    ShortLink.type == link_type, ShortLink.resource_id == resource_id
                )
            )
        ).first()
        if row is None:
            return None
        code, deleted_time = row[0], row[1]
        if deleted_time is not None:
            await session.execute(
                ShortLink.__table__.update()
                .where(ShortLink.code == code)
                .values(deleted_time=None)
            )
            await session.commit()
        return code


async def insert_short_link(link_type: str, resource_id: str, user_id: str) -> str:
    for _ in range(SHORT_LINK_CODE_MAX_RETRY):
        code = random_string(SHORT_LINK_CODE_LENGTH)
        async with db_engine.session() as session:
            try:
                await session.execute(
                    ShortLink.__table__.insert().values(
                        id=cuid(),
                        code=code,
                        type=link_type,
                        resource_id=resource_id,
                        created_by=user_id,
                    )
                )
                await session.commit()
                return code
            except IntegrityError as exc:
                await session.rollback()
                constraint = _violated_constraint(exc)
                if constraint == "short_link_code_key":
                    continue
                reused = await _reuse_existing(link_type, resource_id)
                if reused:
                    return reused
                raise
    raise RuntimeError("Failed to generate short link code")


def _violated_constraint(exc: IntegrityError) -> str | None:
    orig = getattr(exc, "orig", None)
    cause = getattr(orig, "__cause__", None)
    name = getattr(cause, "constraint_name", None)
    if name:
        return name
    text = str(orig or exc)
    if "short_link_code_key" in text:
        return "short_link_code_key"
    if "short_link_type_resource_id_key" in text:
        return "short_link_type_resource_id_key"
    return None
