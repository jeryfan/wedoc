"""waitlist table access."""

from typing import Any

from sqlalchemy import select, update

from ...db import engine as db_engine
from ...db.models_meta import Waitlist


def _row_dict(row: Waitlist) -> dict[str, Any]:
    return {c.name: getattr(row, c.name) for c in Waitlist.__table__.columns}


async def find_by_email(email: str) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        row = (
            await session.execute(select(Waitlist).where(Waitlist.email == email))
        ).scalar_one_or_none()
    return _row_dict(row) if row is not None else None


async def create(email: str) -> dict[str, Any]:
    async with db_engine.session() as session:
        row = Waitlist(email=email)
        session.add(row)
        await session.commit()
    return _row_dict(row)


async def list_all() -> list[dict[str, Any]]:
    async with db_engine.session() as session:
        rows = (
            (await session.execute(select(Waitlist).order_by(Waitlist.created_time.desc())))
            .scalars()
            .all()
        )
    return [_row_dict(row) for row in rows]


async def find_uninvited_by_emails(emails: list[str]) -> list[dict[str, Any]]:
    if not emails:
        return []
    async with db_engine.session() as session:
        rows = (
            (
                await session.execute(
                    select(Waitlist).where(
                        Waitlist.email.in_(emails), Waitlist.invite.isnot(True)
                    )
                )
            )
            .scalars()
            .all()
        )
    return [_row_dict(row) for row in rows]


async def mark_invited(emails: list[str]) -> None:
    from datetime import UTC, datetime

    async with db_engine.session() as session:
        await session.execute(
            update(Waitlist)
            .where(Waitlist.email.in_(emails))
            .values(invite=True, invite_time=datetime.now(tz=UTC).replace(tzinfo=None))
        )
        await session.commit()
