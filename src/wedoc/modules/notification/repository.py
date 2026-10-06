"""Notification persistence — raw-row access, business rules live in service."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select, update

from ...db import engine as db_engine
from ...db.models_meta import Notification, User

NOTIFICATION_LIST_LIMIT = 10

_LIST_COLUMNS = (
    Notification.id,
    Notification.from_user_id,
    Notification.type,
    Notification.url_path,
    Notification.message,
    Notification.message_i18n,
    Notification.severity,
    Notification.is_read,
    Notification.created_time,
)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


async def list_notification_rows(
    user_id: str,
    is_read: bool,
    severity: str | None,
    notify_type: str | None,
    cursor: str | None,
) -> list[dict[str, Any]]:
    async with db_engine.session() as session:
        stmt = select(*_LIST_COLUMNS).where(
            Notification.to_user_id == user_id,
            Notification.is_read.is_(is_read),
        )
        if severity:
            stmt = stmt.where(Notification.severity == severity)
        if notify_type:
            stmt = stmt.where(Notification.type == notify_type)
        stmt = stmt.order_by(Notification.created_time.desc()).limit(NOTIFICATION_LIST_LIMIT + 1)
        if cursor:
            # prisma cursor pagination: skip rows up to and including the cursor,
            # keyed by the same created_time desc order. Resolve the cursor row's
            # created_time then page strictly after it (id tiebreak).
            cursor_time = (
                await session.execute(
                    select(Notification.created_time).where(Notification.id == cursor)
                )
            ).scalar_one_or_none()
            if cursor_time is not None:
                stmt = select(*_LIST_COLUMNS).where(
                    Notification.to_user_id == user_id,
                    Notification.is_read.is_(is_read),
                )
                if severity:
                    stmt = stmt.where(Notification.severity == severity)
                if notify_type:
                    stmt = stmt.where(Notification.type == notify_type)
                stmt = (
                    stmt.where(
                        (Notification.created_time < cursor_time)
                        | (
                            (Notification.created_time == cursor_time)
                            & (Notification.id < cursor)
                        )
                    )
                    .order_by(Notification.created_time.desc())
                    .limit(NOTIFICATION_LIST_LIMIT + 1)
                )
        rows = (await session.execute(stmt)).mappings().all()
    return [
        {
            "id": r["id"],
            "fromUserId": r["from_user_id"],
            "type": r["type"],
            "urlPath": r["url_path"],
            "message": r["message"],
            "messageI18n": r["message_i18n"],
            "severity": r["severity"],
            "isRead": r["is_read"],
            "createdTime": _iso(r["created_time"]),
        }
        for r in rows
    ]


async def summary_by_severity(user_id: str, is_read: bool) -> dict[str, int]:
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(Notification.severity, func.count())
                .where(
                    Notification.to_user_id == user_id,
                    Notification.is_read.is_(is_read),
                )
                .group_by(Notification.severity)
            )
        ).all()
    return {severity: count for severity, count in rows}


async def count_unread(user_id: str) -> int:
    async with db_engine.session() as session:
        return (
            await session.execute(
                select(func.count()).where(
                    Notification.to_user_id == user_id,
                    Notification.is_read.is_(False),
                )
            )
        ).scalar_one()


async def update_status(user_id: str, notification_id: str, is_read: bool) -> None:
    async with db_engine.session() as session:
        await session.execute(
            update(Notification)
            .where(
                Notification.id == notification_id,
                Notification.to_user_id == user_id,
            )
            .values(is_read=is_read)
        )
        await session.commit()


async def mark_all_as_read(user_id: str) -> None:
    async with db_engine.session() as session:
        await session.execute(
            update(Notification)
            .where(
                Notification.to_user_id == user_id,
                Notification.is_read.is_(False),
            )
            .values(is_read=True)
        )
        await session.commit()


async def get_users_by_ids(user_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not user_ids:
        return {}
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(User.id, User.name, User.avatar).where(User.id.in_(user_ids))
            )
        ).all()
    return {r[0]: {"id": r[0], "name": r[1], "avatar": r[2]} for r in rows}


async def insert_notification(row: dict[str, Any]) -> None:
    async with db_engine.session() as session:
        session.add(Notification(**row))
        await session.commit()


async def get_users_by_ids_or_emails(
    user_ids: list[str], emails: list[str]
) -> list[dict[str, str]]:
    conditions = []
    if user_ids:
        conditions.append(User.id.in_(user_ids))
    if emails:
        conditions.append(User.email.in_([e.lower() for e in emails]))
    if not conditions:
        return []
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(User.id, User.email).where(
                    or_(*conditions), User.deleted_time.is_(None)
                )
            )
        ).all()
    return [{"id": r[0], "email": r[1]} for r in rows]
