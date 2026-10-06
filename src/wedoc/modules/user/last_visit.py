"""LastVisitService port: visit resolution and history writes.

Raw-SQL shaped queries follow the upstream knex builders 1:1, including its
quirks (no ordering in the visit lookups, deleted-table tolerance in the CTE).
The workflow/app/routine resource types resolve to an empty last-visit (200).
"""

from typing import Any

from sqlalchemy import and_, or_, select

from ...core import cls
from ...core.errors import ApiError, HttpErrorCode
from ...db import engine as db_engine
from ...db.models_meta import (
    Base,
    Collaborator,
    Dashboard,
    Space,
    TableMeta,
    UserLastVisit,
    View,
)
from . import repository


def _iso(dt: Any) -> str:
    if dt.tzinfo is None:
        return dt.isoformat(timespec="milliseconds") + "Z"
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class LastVisitService:
    async def get_user_last_visit(
        self, user_id: str, resource_type: str, parent_resource_id: str
    ) -> dict[str, Any] | None:
        match resource_type:
            case "space":
                return await self.space_visit(user_id, parent_resource_id)
            case "table":
                return await self.table_visit(user_id, parent_resource_id)
            case "view":
                return await self.view_visit(user_id, parent_resource_id)
            case "dashboard":
                return await self.dashboard_visit(user_id, parent_resource_id)
            case "workflow" | "app" | "routine":
                # EE resolves these to an empty last-visit (no such visit rows at
                # this baseline) -> 200 with no body, same as an unrecorded space.
                return None
            case _:
                raise ApiError(
                    "Invalid resource type",
                    HttpErrorCode.VALIDATION_ERROR,
                    {"localization": {"i18nKey": "httpErrors.lastVisit.invalidResourceType"}},
                )

    async def space_visit(self, user_id: str, parent_resource_id: str) -> dict[str, Any] | None:
        visits = await repository.find_last_visits(
            user_id, resource_types=["space"], parent_resource_id=parent_resource_id
        )
        if not visits:
            return None
        visit = visits[0]
        return {"resourceId": visit["resource_id"], "resourceType": "space"}

    async def table_visit(self, user_id: str, base_id: str) -> dict[str, Any] | None:
        table_visits = await repository.find_last_visits(
            user_id, resource_types=["table"], parent_resource_id=base_id
        )
        table_id = table_visits[0]["resource_id"] if table_visits else None
        # The upstream CTE yields one row per alive visited view; when every
        # visited view is dead the result set is empty and the lookup falls
        # through to the base's default table (not the visited table).
        fell_through = False
        view_id: str | None = None
        if table_id:
            view_visits = await repository.find_last_visits(
                user_id, resource_types=["view"], parent_resource_id=table_id
            )
            for visit in view_visits:
                if await self._view_alive(visit["resource_id"]):
                    view_id = visit["resource_id"]
                    break
            if view_visits and view_id is None:
                fell_through = True
        if table_id and not fell_through:
            if view_id:
                return {"resourceId": table_id, "childResourceId": view_id, "resourceType": "table"}
            table = await self._table_with_first_view(table_id=table_id)
            if table is None:
                return None
            return {
                "resourceId": table["id"],
                "childResourceId": table["views"][0],
                "resourceType": "table",
            }
        table = await self._table_with_first_view(base_id=base_id)
        if table is None:
            return None
        return {
            "resourceId": table["id"],
            "childResourceId": table["views"][0],
            "resourceType": "table",
        }

    async def _table_with_first_view(
        self, table_id: str | None = None, base_id: str | None = None
    ) -> dict[str, Any] | None:
        async with db_engine.session() as session:
            stmt = select(TableMeta).where(TableMeta.deleted_time.is_(None))
            if table_id is not None:
                stmt = stmt.where(TableMeta.id == table_id)
            else:
                stmt = stmt.where(TableMeta.base_id == base_id).order_by(TableMeta.order.asc())
            table = (await session.execute(stmt.limit(1))).scalar_one_or_none()
            if table is None:
                return None
            view = (
                await session.execute(
                    select(View.id)
                    .where(View.table_id == table.id, View.deleted_time.is_(None))
                    .order_by(View.order.asc())
                    .limit(1)
                )
            ).first()
        return {"id": table.id, "views": [view[0]] if view else []}

    async def _view_alive(self, view_id: str) -> bool:
        async with db_engine.session() as session:
            row = (
                await session.execute(
                    select(View.id).where(View.id == view_id, View.deleted_time.is_(None))
                )
            ).first()
        return row is not None

    async def view_visit(self, user_id: str, parent_resource_id: str) -> dict[str, Any] | None:
        visits = await repository.find_last_visits(
            user_id, resource_types=["view"], parent_resource_id=parent_resource_id
        )
        for visit in visits:
            if await self._view_alive(visit["resource_id"]):
                return {"resourceId": visit["resource_id"], "resourceType": "view"}
        async with db_engine.session() as session:
            view = (
                await session.execute(
                    select(View.id)
                    .where(View.table_id == parent_resource_id, View.deleted_time.is_(None))
                    .order_by(View.order.asc())
                    .limit(1)
                )
            ).first()
        if view:
            return {"resourceId": view[0], "resourceType": "view"}
        return None

    async def dashboard_visit(
        self, user_id: str, parent_resource_id: str
    ) -> dict[str, Any] | None:
        visits = await repository.find_last_visits(
            user_id, resource_types=["dashboard"], parent_resource_id=parent_resource_id
        )
        async with db_engine.session() as session:
            for visit in visits:
                alive = (
                    await session.execute(
                        select(Dashboard.id).where(Dashboard.id == visit["resource_id"])
                    )
                ).first()
                if alive:
                    return {"resourceId": visit["resource_id"], "resourceType": "dashboard"}
            dashboard = (
                await session.execute(
                    select(Dashboard.id).where(Dashboard.base_id == parent_resource_id).limit(1)
                )
            ).first()
        if dashboard:
            return {"resourceId": dashboard[0], "resourceType": "dashboard"}
        return None

    async def base_node_visit(
        self, user_id: str, parent_resource_id: str
    ) -> dict[str, Any] | None:
        visits = await repository.find_last_visits(
            user_id,
            resource_types=["table", "dashboard", "workflow", "app"],
            parent_resource_id=parent_resource_id,
        )
        if not visits:
            return None
        return {"resourceId": visits[0]["resource_id"], "resourceType": visits[0]["resource_type"]}

    async def list_base(self, user_id: str) -> dict[str, Any]:
        departments = cls.get("organization.departments") or []
        principal_ids = [d["id"] for d in departments] + [user_id]
        ulv = UserLastVisit
        base, space, collab = Base, Space, Collaborator
        async with db_engine.session() as session:
            stmt = (
                select(
                    ulv.resource_id,
                    ulv.resource_type,
                    ulv.last_visit_time,
                    base.name,
                    base.icon,
                    collab.role_name,
                    space.id.label("space_id"),
                    base.created_by,
                )
                .select_from(ulv)
                .join(base, and_(base.id == ulv.resource_id, base.deleted_time.is_(None)))
                .join(space, and_(space.id == ulv.parent_resource_id, space.deleted_time.is_(None)))
                .join(
                    collab,
                    and_(
                        collab.principal_id.in_(principal_ids),
                        or_(
                            collab.resource_id == ulv.parent_resource_id,
                            collab.resource_id == ulv.resource_id,
                        ),
                    ),
                )
                .where(ulv.user_id == user_id, ulv.resource_type == "base")
                .order_by(ulv.last_visit_time.desc())
            )
            rows = (await session.execute(stmt)).all()
        seen: set[tuple] = set()
        items: list[dict[str, Any]] = []
        for row in rows:
            key = tuple(row)
            if key in seen:
                continue
            seen.add(key)
            items.append(
                {
                    "resourceId": row[0],
                    "resourceType": row[1],
                    "lastVisitTime": _iso(row[2]),
                    "resource": {
                        "id": row[0],
                        "name": row[3],
                        "icon": row[4],
                        "role": row[5],
                        "spaceId": row[6],
                        "createdBy": row[7],
                    },
                }
            )
        return {"total": len(items), "list": items}

    async def get_user_last_visit_map(
        self, user_id: str, parent_resource_id: str
    ) -> dict[str, Any]:
        async with db_engine.session() as session:
            tables = (
                (
                    await session.execute(
                        select(TableMeta.id).where(
                            TableMeta.base_id == parent_resource_id,
                            TableMeta.deleted_time.is_(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
        visits = await repository.find_last_visits(
            user_id,
            resource_types=["view"],
            parent_resource_ids=list(tables) or ["__none__"],
        )
        results: list[dict[str, Any]] = []
        for visit in visits:
            if await self._view_alive(visit["resource_id"]):
                # the knex query selects only resourceId/parentResourceId — no
                # resourceType key on visited entries (verified on the wire)
                results.append(
                    {
                        "resourceId": visit["resource_id"],
                        "parentResourceId": visit["parent_resource_id"],
                    }
                )
        visited_tables = {r["parentResourceId"] for r in results}
        for table_id in tables:
            if table_id in visited_tables:
                continue
            async with db_engine.session() as session:
                view = (
                    await session.execute(
                        select(View.id)
                        .where(View.table_id == table_id, View.deleted_time.is_(None))
                        .order_by(View.order.asc())
                        .limit(1)
                    )
                ).first()
            if view:
                results.append(
                    {
                        "resourceId": view[0],
                        "parentResourceId": table_id,
                        "resourceType": "view",
                    }
                )
        return {r["parentResourceId"]: r for r in results}

    async def update_user_last_visit(self, user_id: str, data: dict[str, Any]) -> None:
        resource_type = data["resourceType"]
        resource_id = data["resourceId"]
        parent_resource_id = data["parentResourceId"]
        child_resource_id = data.get("childResourceId")
        if resource_type == "base":
            await repository.upsert_last_visit(user_id, "base", resource_id, parent_resource_id)
            return
        await repository.upsert_last_visit(user_id, resource_type, resource_id, parent_resource_id)
        await repository.prune_last_visits(user_id, resource_type, parent_resource_id, keep=1)
        if child_resource_id:
            await repository.upsert_last_visit(user_id, "view", child_resource_id, resource_id)
            await repository.prune_last_visits(user_id, "view", resource_id, keep=1)
