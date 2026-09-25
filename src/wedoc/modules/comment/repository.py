"""Comment / comment-subscription persistence — raw-row access only."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, delete, func, insert, or_, select, update

from ...db import engine as db_engine
from ...db.models_meta import Comment, CommentSubscription, User


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _row(comment: Comment) -> dict[str, Any]:
    return {
        "id": comment.id,
        "tableId": comment.table_id,
        "recordId": comment.record_id,
        "quoteId": comment.quote_Id,
        "content": comment.content,
        "reaction": comment.reaction,
        "deletedTime": comment.deleted_time,
        "createdTime": comment.created_time,
        "createdBy": comment.created_by,
        "lastModifiedTime": comment.last_modified_time,
    }


async def find_comment(
    table_id: str, record_id: str, comment_id: str
) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(Comment).where(
                    Comment.id == comment_id,
                    Comment.table_id == table_id,
                    Comment.record_id == record_id,
                    Comment.deleted_time.is_(None),
                )
            )
        ).scalar_one_or_none()
    return _row(row) if row else None


async def find_comment_created_by(
    table_id: str, record_id: str, comment_id: str
) -> str | None:
    async with db_engine.session() as session:
        return (
            await session.execute(
                select(Comment.created_by).where(
                    Comment.id == comment_id,
                    Comment.table_id == table_id,
                    Comment.record_id == record_id,
                    Comment.deleted_time.is_(None),
                )
            )
        ).scalar_one_or_none()


async def create_comment(
    comment_id: str,
    table_id: str,
    record_id: str,
    content: str,
    created_by: str,
    quote_id: str | None,
) -> dict[str, Any]:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                insert(Comment)
                .values(
                    id=comment_id,
                    table_id=table_id,
                    record_id=record_id,
                    content=content,
                    created_by=created_by,
                    quote_Id=quote_id,
                    last_modified_time=None,
                )
                .returning(Comment)
            )
        ).scalar_one()
        await session.commit()
        return _row(row)


async def update_comment_content(
    table_id: str,
    record_id: str,
    comment_id: str,
    created_by: str,
    content: str,
    last_modified_time: datetime,
) -> int:
    async with db_engine.session() as session:
        result = await session.execute(
            update(Comment)
            .where(
                Comment.id == comment_id,
                Comment.table_id == table_id,
                Comment.record_id == record_id,
                Comment.deleted_time.is_(None),
                Comment.created_by == created_by,
            )
            .values(content=content, last_modified_time=last_modified_time)
        )
        await session.commit()
        return result.rowcount


async def soft_delete_comment(
    table_id: str, record_id: str, comment_id: str, created_by: str, deleted_time: datetime
) -> int:
    async with db_engine.session() as session:
        result = await session.execute(
            update(Comment)
            .where(
                Comment.id == comment_id,
                Comment.table_id == table_id,
                Comment.record_id == record_id,
                Comment.deleted_time.is_(None),
                Comment.created_by == created_by,
            )
            .values(deleted_time=deleted_time)
        )
        await session.commit()
        return result.rowcount


async def update_reaction(
    table_id: str,
    record_id: str,
    comment_id: str,
    reaction: str | None,
    last_modified_time: datetime | None,
) -> int:
    async with db_engine.session() as session:
        result = await session.execute(
            update(Comment)
            .where(
                Comment.id == comment_id,
                Comment.table_id == table_id,
                Comment.record_id == record_id,
                Comment.deleted_time.is_(None),
            )
            .values(reaction=reaction, last_modified_time=last_modified_time)
        )
        await session.commit()
        return result.rowcount


async def list_comments(
    table_id: str,
    record_id: str,
    take_with_direction: int,
    cursor: str | None,
    skip: int,
) -> list[dict[str, Any]]:
    """prisma findMany with negative take (reverse) and cursor semantics."""
    limit = abs(take_with_direction)
    reverse = take_with_direction < 0
    async with db_engine.session() as session:
        cursor_time = None
        if cursor:
            cursor_time = (
                await session.execute(
                    select(Comment.created_time).where(Comment.id == cursor)
                )
            ).scalar_one_or_none()

        cols = (
            Comment.id,
            Comment.content,
            Comment.created_by,
            Comment.created_time,
            Comment.last_modified_time,
            Comment.quote_Id,
            Comment.reaction,
        )
        base_where = [
            Comment.record_id == record_id,
            Comment.table_id == table_id,
            Comment.deleted_time.is_(None),
        ]
        # prisma: negative take walks backward from the cursor (rows ordered asc
        # then the last |take| are returned); positive take walks forward.
        if reverse:
            stmt = select(*cols).where(*base_where)
            if cursor_time is not None:
                if skip == 0:
                    stmt = stmt.where(
                        or_(
                            Comment.created_time < cursor_time,
                            and_(
                                Comment.created_time == cursor_time,
                                Comment.id <= cursor,
                            ),
                        )
                    )
                else:
                    stmt = stmt.where(
                        or_(
                            Comment.created_time < cursor_time,
                            and_(
                                Comment.created_time == cursor_time,
                                Comment.id < cursor,
                            ),
                        )
                    )
            stmt = stmt.order_by(
                Comment.created_time.desc(), Comment.id.desc()
            ).limit(limit)
            rows = (await session.execute(stmt)).mappings().all()
            rows = list(reversed(rows))
        else:
            stmt = select(*cols).where(*base_where)
            if cursor_time is not None:
                if skip == 0:
                    stmt = stmt.where(
                        or_(
                            Comment.created_time > cursor_time,
                            and_(
                                Comment.created_time == cursor_time,
                                Comment.id >= cursor,
                            ),
                        )
                    )
                else:
                    stmt = stmt.where(
                        or_(
                            Comment.created_time > cursor_time,
                            and_(
                                Comment.created_time == cursor_time,
                                Comment.id > cursor,
                            ),
                        )
                    )
            stmt = stmt.order_by(
                Comment.created_time.asc(), Comment.id.asc()
            ).limit(limit)
            rows = (await session.execute(stmt)).mappings().all()

    return [
        {
            "id": r["id"],
            "content": r["content"],
            "createdBy": r["created_by"],
            "createdTime": r["created_time"],
            "lastModifiedTime": r["last_modified_time"],
            "quoteId": r["quote_Id"],
            "reaction": r["reaction"],
        }
        for r in rows
    ]


async def count_record_comments(table_id: str, record_id: str) -> int:
    async with db_engine.session() as session:
        return (
            await session.execute(
                select(func.count()).where(
                    Comment.table_id == table_id,
                    Comment.record_id == record_id,
                    Comment.deleted_time.is_(None),
                )
            )
        ).scalar_one()


async def group_counts_by_records(
    table_id: str, record_ids: list[str]
) -> list[dict[str, Any]]:
    if not record_ids:
        return []
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(Comment.record_id, func.count())
                .where(
                    Comment.record_id.in_(record_ids),
                    Comment.table_id == table_id,
                    Comment.deleted_time.is_(None),
                )
                .group_by(Comment.record_id)
            )
        ).all()
    return [{"recordId": rid, "count": count} for rid, count in rows]


async def get_subscription(
    table_id: str, record_id: str
) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(
                    CommentSubscription.table_id,
                    CommentSubscription.record_id,
                    CommentSubscription.created_by,
                ).where(
                    CommentSubscription.table_id == table_id,
                    CommentSubscription.record_id == record_id,
                )
            )
        ).mappings().first()
    if not row:
        return None
    return {
        "tableId": row["table_id"],
        "recordId": row["record_id"],
        "createdBy": row["created_by"],
    }


async def create_subscription(table_id: str, record_id: str, created_by: str) -> None:
    # comment_subscription.id is prisma @default(cuid()) — no explicit id upstream.
    from ...core.ids import cuid

    async with db_engine.session() as session:
        await session.execute(
            insert(CommentSubscription).values(
                id=cuid(),
                table_id=table_id,
                record_id=record_id,
                created_by=created_by,
            )
        )
        await session.commit()


async def delete_subscription(table_id: str, record_id: str) -> int:
    async with db_engine.session() as session:
        result = await session.execute(
            delete(CommentSubscription).where(
                CommentSubscription.table_id == table_id,
                CommentSubscription.record_id == record_id,
            )
        )
        await session.commit()
        return result.rowcount


async def list_subscribers(table_id: str, record_id: str) -> list[str]:
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(CommentSubscription.created_by).where(
                    CommentSubscription.table_id == table_id,
                    CommentSubscription.record_id == record_id,
                )
            )
        ).all()
    return [r[0] for r in rows]


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
