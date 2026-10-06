"""Template service — ports template-open-api.service.ts + template-permalink.service.ts.

The snapshot path duplicates the template's source base into the template space via
BaseService.duplicate_base_impl, soft-deletes the previous snapshot base, and records
the new snapshot descriptor on the template row.
"""

import json
from datetime import UTC, datetime
from typing import Any

from ...core import cls
from ...core.errors import ApiError, HttpErrorCode
from ...core.ids import IdPrefix, new_id
from ...core.storage import get_public_full_storage_url
from . import repository
from .schemas import (
    CreateTemplateCategoryRo,
    CreateTemplateRo,
    TemplateListQueryRo,
    TemplateQueryRo,
    UpdateOrderRo,
    UpdateTemplateCategoryRo,
    UpdateTemplateRo,
)

_EPSILON2 = 2 * 2.220446049250313e-16
_MAX_CATEGORY = 50
_LIST_KEYS = (
    "id",
    "name",
    "cover",
    "snapshot",
    "createdBy",
    "categoryId",
    "isSystem",
    "featured",
    "isPublished",
    "description",
    "baseId",
    "usageCount",
    "markdownDescription",
    "publishInfo",
    "visitCount",
)


def _validate_take(take: int) -> None:
    if take and take > 1000:
        raise ApiError(
            "Take count is too large",
            HttpErrorCode.VALIDATION_ERROR,
            {"localization": {"i18nKey": "httpErrors.template.takeCountTooLarge"}},
        )


class TemplateService:
    async def _user_map(self, user_ids: list[str]) -> dict[str, Any]:
        raw = await repository.get_users_by_ids(user_ids)
        out: dict[str, Any] = {}
        for uid, u in raw.items():
            user: dict[str, Any] = {"id": u["id"], "name": u["name"], "email": u["email"]}
            # ref sets avatar: user.avatar ? url : undefined -> key omitted when null
            if u["avatar"]:
                user["avatar"] = get_public_full_storage_url(u["avatar"])
            out[uid] = user
        return out

    def _cover_obj(self, cover: str | None, *, empty_when_null: bool) -> Any:
        if not cover:
            return {} if empty_when_null else None
        parsed = json.loads(cover)
        path = parsed.get("path")
        return {**parsed, "presignedUrl": get_public_full_storage_url(path) if path else None}

    def _transform_list_item(
        self, row: dict[str, Any], user_map: dict[str, Any], keys: tuple[str, ...] | None
    ) -> dict[str, Any]:
        item = {k: row[k] for k in keys} if keys else dict(row)
        # order is a double column; JS serializes a whole value as an int.
        if isinstance(item.get("order"), float) and item["order"].is_integer():
            item["order"] = int(item["order"])
        if row.get("cover"):
            parsed = json.loads(row["cover"])
            path = parsed.get("path")
            thumbnail = parsed.get("thumbnailPath") or {}
            final = thumbnail.get("lg") or path
            item["cover"] = {
                **parsed,
                "presignedUrl": get_public_full_storage_url(final) if final else None,
            }
        else:
            item.pop("cover", None)
        if row.get("snapshot"):
            item["snapshot"] = json.loads(row["snapshot"])
        else:
            item.pop("snapshot", None)
        item["createdBy"] = user_map.get(row["createdBy"])
        return item

    async def create_template(self, ro: CreateTemplateRo) -> dict[str, Any]:
        user_id = cls.get("user.id")
        template_id = new_id(IdPrefix.TEMPLATE)
        max_order = await repository.max_template_order()
        order = max_order + 1 if isinstance(max_order, (int, float)) else 1
        return await repository.create_template(
            template_id, ro.name, ro.description, user_id, order
        )

    async def get_all_template_list(
        self, query: TemplateListQueryRo | None
    ) -> list[dict[str, Any]]:
        skip = query.skip if query and query.skip is not None else 0
        take = query.take if query and query.take is not None else 300
        _validate_take(take)
        rows = await repository.list_all_templates(skip, take)
        user_map = await self._user_map([r["createdBy"] for r in rows])
        return [self._transform_list_item(r, user_map, _LIST_KEYS) for r in rows]

    async def get_published_template_list(
        self, query: TemplateQueryRo | None
    ) -> list[dict[str, Any]]:
        skip = query.skip if query and query.skip is not None else 0
        take = query.take if query and query.take is not None else 100
        _validate_take(take)
        rows = await repository.list_published_templates(
            skip,
            take,
            query.featured if query else None,
            query.categoryId if query else None,
            query.search if query else None,
        )
        user_map = await self._user_map([r["createdBy"] for r in rows])
        return [self._transform_list_item(r, user_map, None) for r in rows]

    async def delete_template(self, template_id: str) -> dict[str, Any]:
        return await repository.delete_template_row(template_id)

    async def update_template(self, template_id: str, ro: UpdateTemplateRo) -> None:
        original = await repository.get_template_or_500(template_id)
        provided = ro.model_dump(exclude_unset=True)
        if provided.get("isPublished") and not original.get("snapshot"):
            raise ApiError(
                "This template could not be published, causing the lacking of snapshot",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.template.snapshotRequired"}},
            )
        column_map = {
            "name": "name",
            "description": "description",
            "categoryId": "category_id",
            "isPublished": "is_published",
            "featured": "featured",
            "isSystem": "is_system",
            "baseId": "base_id",
            "markdownDescription": "markdown_description",
        }
        values: dict[str, Any] = {}
        for ro_key, col in column_map.items():
            if ro_key in provided:
                values[col] = provided[ro_key]
        if "cover" in provided:
            cover = provided["cover"]
            values["cover"] = json.dumps(cover) if cover else cover
        values["last_modified_time"] = datetime.now(UTC).replace(tzinfo=None)
        await repository.update_template_row(template_id, values)

    async def pin_top_template(self, template_id: str) -> None:
        min_order = await repository.min_template_order()
        if not isinstance(min_order, (int, float)):
            raise ApiError(
                "No min order found",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.template.noMinOrderFound"}},
            )
        await repository.update_template_row(template_id, {"order": min_order - 1})

    async def create_template_snapshot(self, template_id: str) -> dict[str, Any]:
        from ..base import repository as base_repository
        from ..base.service import BaseService

        template = await repository.get_template_or_500(template_id)
        source_base_id = template.get("baseId")
        if not source_base_id:
            raise ApiError(
                "Source template not found",
                HttpErrorCode.NOT_FOUND,
                {"localization": {"i18nKey": "httpErrors.template.sourceTemplateNotFound"}},
            )
        base_service = BaseService()
        template_space_id = await base_service._template_space_id()
        snapshot = await base_service.duplicate_base_impl(
            source_base_id, template_space_id, True, template.get("name") or "template snapshot"
        )
        now = datetime.now(UTC)
        now_naive = now.replace(tzinfo=None)
        previous = json.loads(template["snapshot"]) if template.get("snapshot") else None
        if previous and previous.get("baseId"):
            await base_repository.update_base_row(previous["baseId"], {"deleted_time": now_naive})
        snapshot_json = json.dumps(
            {
                "baseId": snapshot["id"],
                "snapshotTime": now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "spaceId": snapshot["spaceId"],
                "name": snapshot["name"],
            },
            separators=(",", ":"),
        )
        await repository.update_template_row(
            template_id,
            {
                "snapshot": snapshot_json,
                "last_modified_by": cls.get("user.id"),
                "last_modified_time": now_naive,
            },
        )
        return await repository.get_template_or_500(template_id)

    async def get_template_detail_by_id(self, template_id: str) -> dict[str, Any]:
        template = await repository.get_template_or_500(template_id)
        user_map = await self._user_map([template["createdBy"]])
        result = dict(template)
        result["cover"] = self._cover_obj(template["cover"], empty_when_null=True)
        if template["snapshot"]:
            result["snapshot"] = json.loads(template["snapshot"])
        else:
            result.pop("snapshot", None)
        result["createdBy"] = user_map.get(template["createdBy"])
        if result["createdBy"] is None:
            result.pop("createdBy", None)
        return result

    async def get_template_by_base_id(self, base_id: str) -> dict[str, Any] | None:
        template = await repository.get_template_by_base(base_id)
        if template is None:
            return None
        subset = {k: template[k] for k in _LIST_KEYS}
        subset["cover"] = self._cover_obj(template["cover"], empty_when_null=False)
        subset["snapshot"] = json.loads(template["snapshot"]) if template["snapshot"] else None
        user_map = await self._user_map([template["createdBy"]])
        subset["createdBy"] = user_map.get(template["createdBy"])
        return subset

    async def increment_template_visit_count(self, template_id: str) -> None:
        await repository.increment_visit(template_id)

    async def _shuffle_templates(self) -> None:
        ids = await repository.all_templates_ordered()
        for i, tid in enumerate(ids, start=1):
            await repository.update_template_row(tid, {"order": float(i)})

    async def update_order(self, template_id: str, ro: UpdateOrderRo) -> None:
        orders = await repository.all_template_orders()
        if len(set(orders)) != len(orders):
            await self._shuffle_templates()
        item = await repository.find_template_order(template_id)
        if item is None:
            raise ApiError(
                "Template not found",
                HttpErrorCode.NOT_FOUND,
                {"localization": {"i18nKey": "httpErrors.base.templateNotFound"}},
            )
        anchor = await repository.find_template_order(ro.anchorId)
        if anchor is None:
            raise ApiError(
                "Anchor template not found",
                HttpErrorCode.NOT_FOUND,
                {
                    "localization": {
                        "i18nKey": "httpErrors.table.anchorNotFound",
                        "context": {"anchorId": ro.anchorId},
                    }
                },
            )
        await self._reorder(ro.position, item, anchor, repository.get_next_template,
                            repository.update_template_row, self._shuffle_templates)

    async def _reorder(self, position, item, anchor, get_next, update_row, shuffle) -> None:
        before = position == "before"
        op = "lt" if before else "gt"
        align = "desc" if before else "asc"
        nxt = await get_next(anchor["order"], op, align)
        if nxt:
            order = (nxt["order"] + anchor["order"]) / 2
        else:
            order = anchor["order"] + (-1 if before else 1)
        if abs(order - anchor["order"]) < _EPSILON2:
            await shuffle()
            await self._reorder(position, item, anchor, get_next, update_row, shuffle)
            return
        await update_row(item["id"], {"order": order})

    # --- categories --------------------------------------------------------

    async def create_template_category(self, ro: CreateTemplateCategoryRo) -> dict[str, Any]:
        user_id = cls.get("user.id")
        count = await repository.count_categories()
        if count >= _MAX_CATEGORY:
            raise ApiError(
                f"Template category limit reached (max {_MAX_CATEGORY})",
                HttpErrorCode.VALIDATION_ERROR,
                {
                    "localization": {
                        "i18nKey": "httpErrors.template.categoryLimitReached",
                        "context": {"maxCount": _MAX_CATEGORY},
                    }
                },
            )
        category_id = new_id(IdPrefix.TEMPLATE_CATEGORY)
        max_order = await repository.max_category_order()
        order = max_order + 1 if isinstance(max_order, (int, float)) else 1
        return await repository.create_category(category_id, ro.name, user_id, order)

    async def get_template_category_list(self) -> list[dict[str, Any]]:
        return await repository.list_categories()

    async def delete_template_category(self, category_id: str) -> None:
        await repository.delete_category_row(category_id)

    async def update_template_category(
        self, category_id: str, ro: UpdateTemplateCategoryRo
    ) -> None:
        await repository.update_category_row(category_id, ro.name)

    async def update_template_category_order(self, category_id: str, ro: UpdateOrderRo) -> None:
        from . import repository as repo

        orders = await repo.all_category_orders()
        if len(set(orders)) != len(orders):
            await self._shuffle_categories()
        item = await repo.find_category_order(category_id)
        if item is None:
            raise ApiError(
                "Template category not found",
                HttpErrorCode.NOT_FOUND,
                {"localization": {"i18nKey": "httpErrors.template.categoryNotFound"}},
            )
        anchor = await repo.find_category_order(ro.anchorId)
        if anchor is None:
            raise ApiError(
                "Anchor template category not found",
                HttpErrorCode.NOT_FOUND,
                {
                    "localization": {
                        "i18nKey": "httpErrors.table.anchorNotFound",
                        "context": {"anchorId": ro.anchorId},
                    }
                },
            )
        await self._reorder(ro.position, item, anchor, repo.get_next_category,
                            repo.update_category_order_value, self._shuffle_categories)

    async def _shuffle_categories(self) -> None:
        from . import repository as repo

        ids = await repo.all_categories_ordered()
        for i, cid in enumerate(ids, start=1):
            await repo.update_category_order_value(cid, {"order": float(i)})

    # --- permalink ---------------------------------------------------------

    async def resolve_permalink(self, identifier: str) -> dict[str, Any]:
        if not identifier.startswith(str(IdPrefix.TEMPLATE)):
            raise ApiError("Invalid identifier", HttpErrorCode.NOT_FOUND)
        template = await repository.get_template_by_id_optional(identifier)
        if template is None:
            raise ApiError("Template not found", HttpErrorCode.NOT_FOUND)
        if not template["isPublished"]:
            raise ApiError("Template is not published", HttpErrorCode.RESTRICTED_RESOURCE)
        snapshot = json.loads(template["snapshot"]) if template["snapshot"] else {}
        snapshot_base_id = snapshot.get("baseId")
        if not snapshot_base_id:
            raise ApiError("Template snapshot is invalid", HttpErrorCode.UNPROCESSABLE_ENTITY)
        publish_info = template["publishInfo"] or {}
        default_url = publish_info.get("defaultUrl") if isinstance(publish_info, dict) else None
        return {"redirectUrl": default_url or f"/base/{snapshot_base_id}"}
