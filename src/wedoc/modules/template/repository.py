"""Template + template-category persistence."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from ...core.errors import ApiError, HttpErrorCode
from ...db import engine as db_engine
from ...db.models_meta import Template, TemplateCategory, User

_TEMPLATE_COLUMNS = (
    Template.id,
    Template.base_id,
    Template.cover,
    Template.name,
    Template.description,
    Template.markdown_description,
    Template.category_id,
    Template.created_time,
    Template.created_by,
    Template.last_modified_time,
    Template.last_modified_by,
    Template.is_system,
    Template.is_published,
    Template.featured,
    Template.snapshot,
    Template.order,
    Template.usage_count,
    Template.publish_info,
    Template.visit_count,
)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _template_row(m: Any) -> dict[str, Any]:
    return {
        "id": m["id"],
        "baseId": m["base_id"],
        "cover": m["cover"],
        "name": m["name"],
        "description": m["description"],
        "markdownDescription": m["markdown_description"],
        "categoryId": m["category_id"] if m["category_id"] is not None else [],
        "createdTime": _iso(m["created_time"]),
        "createdBy": m["created_by"],
        "lastModifiedTime": _iso(m["last_modified_time"]),
        "lastModifiedBy": m["last_modified_by"],
        "isSystem": m["is_system"],
        "isPublished": m["is_published"],
        "featured": m["featured"],
        "snapshot": m["snapshot"],
        "order": m["order"],
        "usageCount": m["usage_count"],
        "publishInfo": m["publish_info"],
        "visitCount": m["visit_count"],
    }


def _category_row(m: Any) -> dict[str, Any]:
    return {
        "id": m["id"],
        "name": m["name"],
        "createdTime": _iso(m["created_time"]),
        "createdBy": m["created_by"],
        "lastModifiedTime": _iso(m["last_modified_time"]),
        "lastModifiedBy": m["last_modified_by"],
        "order": m["order"],
    }


async def max_template_order() -> float | None:
    async with db_engine.session() as session:
        return (await session.execute(select(func.max(Template.order)))).scalar()


async def min_template_order() -> float | None:
    async with db_engine.session() as session:
        return (await session.execute(select(func.min(Template.order)))).scalar()


async def create_template(
    template_id: str, name: str | None, description: str | None, user_id: str, order: float
) -> dict[str, Any]:
    now = datetime.now(UTC).replace(tzinfo=None)
    async with db_engine.session() as session:
        await session.execute(
            Template.__table__.insert().values(
                id=template_id,
                name=name,
                description=description,
                category_id=[],
                created_by=user_id,
                created_time=now,
                last_modified_time=now,
                order=order,
            )
        )
        await session.commit()
        row = (
            await session.execute(select(*_TEMPLATE_COLUMNS).where(Template.id == template_id))
        ).mappings().one()
    return _template_row(row)


async def list_all_templates(skip: int, take: int) -> list[dict[str, Any]]:
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(*_TEMPLATE_COLUMNS).order_by(Template.order.asc()).offset(skip).limit(take)
            )
        ).mappings().all()
    return [_template_row(r) for r in rows]


async def list_published_templates(
    skip: int, take: int, featured: bool | None, category_id: str | None, search: str | None
) -> list[dict[str, Any]]:
    async with db_engine.session() as session:
        stmt = select(*_TEMPLATE_COLUMNS).where(Template.is_published.is_(True))
        if featured is True:
            stmt = stmt.where(Template.featured.is_(True))
        elif featured is False:
            stmt = stmt.where(
                (Template.featured.is_(False)) | (Template.featured.is_(None))
            )
        if category_id:
            stmt = stmt.where(Template.category_id.any(category_id))
        if search:
            stmt = stmt.where(Template.name.ilike(f"%{search}%"))
        stmt = stmt.order_by(Template.order.asc()).offset(skip).limit(take)
        rows = (await session.execute(stmt)).mappings().all()
    return [_template_row(r) for r in rows]


async def get_template_or_500(template_id: str) -> dict[str, Any]:
    async with db_engine.session() as session:
        row = (
            await session.execute(select(*_TEMPLATE_COLUMNS).where(Template.id == template_id))
        ).mappings().one_or_none()
    if row is None:
        raise ApiError("Internal Server Error", HttpErrorCode.INTERNAL_SERVER_ERROR)
    return _template_row(row)


async def get_template_by_base(base_id: str) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        row = (
            await session.execute(select(*_TEMPLATE_COLUMNS).where(Template.base_id == base_id))
        ).mappings().one_or_none()
    return _template_row(row) if row else None


async def find_template_order(template_id: str) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(Template.id, Template.order).where(Template.id == template_id)
            )
        ).first()
    return {"id": row[0], "order": row[1]} if row else None


async def update_template_row(template_id: str, values: dict[str, Any]) -> None:
    async with db_engine.session() as session:
        result = await session.execute(
            Template.__table__.update().where(Template.id == template_id).values(**values)
        )
        if result.rowcount == 0:
            # prisma update on a missing row throws P2025 -> unmapped 500
            raise ApiError("Internal Server Error", HttpErrorCode.INTERNAL_SERVER_ERROR)
        await session.commit()


async def delete_template_row(template_id: str) -> dict[str, Any]:
    async with db_engine.session() as session:
        row = (
            await session.execute(select(*_TEMPLATE_COLUMNS).where(Template.id == template_id))
        ).mappings().one_or_none()
        if row is None:
            raise ApiError("Internal Server Error", HttpErrorCode.INTERNAL_SERVER_ERROR)
        result = _template_row(row)
        await session.execute(Template.__table__.delete().where(Template.id == template_id))
        await session.commit()
    return result


async def increment_visit(template_id: str) -> None:
    async with db_engine.session() as session:
        await session.execute(
            Template.__table__.update()
            .where(Template.id == template_id)
            .values(visit_count=Template.visit_count + 1)
        )
        await session.commit()


async def get_next_template(where_order: float, op: str, align: str) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        stmt = select(Template.id, Template.order).where(
            Template.order < where_order if op == "lt" else Template.order > where_order
        ).order_by(Template.order.desc() if align == "desc" else Template.order.asc())
        row = (await session.execute(stmt)).first()
    return {"id": row[0], "order": row[1]} if row else None


async def all_template_orders() -> list[float]:
    async with db_engine.session() as session:
        return list((await session.execute(select(Template.order))).scalars().all())


async def all_templates_ordered() -> list[str]:
    async with db_engine.session() as session:
        return list(
            (await session.execute(select(Template.id).order_by(Template.order.asc())))
            .scalars()
            .all()
        )


# --- categories ------------------------------------------------------------


async def count_categories() -> int:
    async with db_engine.session() as session:
        result = await session.execute(select(func.count()).select_from(TemplateCategory))
        return result.scalar_one()


async def max_category_order() -> float | None:
    async with db_engine.session() as session:
        return (await session.execute(select(func.max(TemplateCategory.order)))).scalar()


async def create_category(
    category_id: str, name: str, user_id: str, order: float
) -> dict[str, Any]:
    now = datetime.now(UTC).replace(tzinfo=None)
    async with db_engine.session() as session:
        await session.execute(
            TemplateCategory.__table__.insert().values(
                id=category_id,
                name=name,
                created_by=user_id,
                created_time=now,
                last_modified_time=now,
                order=order,
            )
        )
        await session.commit()
        row = (
            await session.execute(
                select(
                    TemplateCategory.id,
                    TemplateCategory.name,
                    TemplateCategory.created_time,
                    TemplateCategory.created_by,
                    TemplateCategory.last_modified_time,
                    TemplateCategory.last_modified_by,
                    TemplateCategory.order,
                ).where(TemplateCategory.id == category_id)
            )
        ).mappings().one()
    return _category_row(row)


async def list_categories() -> list[dict[str, Any]]:
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(
                    TemplateCategory.id,
                    TemplateCategory.name,
                    TemplateCategory.created_time,
                    TemplateCategory.created_by,
                    TemplateCategory.last_modified_time,
                    TemplateCategory.last_modified_by,
                    TemplateCategory.order,
                )
                .order_by(TemplateCategory.order.asc())
                .limit(50)
            )
        ).mappings().all()
    return [_category_row(r) for r in rows]


async def delete_category_row(category_id: str) -> None:
    async with db_engine.session() as session:
        result = await session.execute(
            TemplateCategory.__table__.delete().where(TemplateCategory.id == category_id)
        )
        if result.rowcount == 0:
            raise ApiError("Internal Server Error", HttpErrorCode.INTERNAL_SERVER_ERROR)
        await session.commit()


async def update_category_row(category_id: str, name: str) -> None:
    async with db_engine.session() as session:
        result = await session.execute(
            TemplateCategory.__table__.update()
            .where(TemplateCategory.id == category_id)
            .values(name=name)
        )
        if result.rowcount == 0:
            raise ApiError("Internal Server Error", HttpErrorCode.INTERNAL_SERVER_ERROR)
        await session.commit()


async def all_category_orders() -> list[float]:
    async with db_engine.session() as session:
        return list((await session.execute(select(TemplateCategory.order))).scalars().all())


async def all_categories_ordered() -> list[str]:
    async with db_engine.session() as session:
        return list(
            (
                await session.execute(
                    select(TemplateCategory.id).order_by(TemplateCategory.order.asc())
                )
            )
            .scalars()
            .all()
        )


async def find_category_order(category_id: str) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        row = (
            await session.execute(
                select(TemplateCategory.id, TemplateCategory.order).where(
                    TemplateCategory.id == category_id
                )
            )
        ).first()
    return {"id": row[0], "order": row[1]} if row else None


async def get_next_category(where_order: float, op: str, align: str) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        stmt = (
            select(TemplateCategory.id, TemplateCategory.order)
            .where(
                TemplateCategory.order < where_order
                if op == "lt"
                else TemplateCategory.order > where_order
            )
            .order_by(
                TemplateCategory.order.desc() if align == "desc" else TemplateCategory.order.asc()
            )
        )
        row = (await session.execute(stmt)).first()
    return {"id": row[0], "order": row[1]} if row else None


async def update_category_order_value(category_id: str, values: dict[str, Any]) -> None:
    async with db_engine.session() as session:
        await session.execute(
            TemplateCategory.__table__.update()
            .where(TemplateCategory.id == category_id)
            .values(**values)
        )
        await session.commit()


async def get_template_by_id_optional(template_id: str) -> dict[str, Any] | None:
    async with db_engine.session() as session:
        row = (
            await session.execute(select(*_TEMPLATE_COLUMNS).where(Template.id == template_id))
        ).mappings().one_or_none()
    return _template_row(row) if row else None


async def get_users_by_ids(user_ids: list[str]) -> dict[str, dict[str, Any]]:
    ids = [uid for uid in user_ids if uid]
    if not ids:
        return {}
    async with db_engine.session() as session:
        rows = (
            await session.execute(
                select(User.id, User.name, User.avatar, User.email).where(
                    User.id.in_(ids), User.deleted_time.is_(None)
                )
            )
        ).all()
    return {r[0]: {"id": r[0], "name": r[1], "avatar": r[2], "email": r[3]} for r in rows}
