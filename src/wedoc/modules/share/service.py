"""share domain service — ports share.service.ts (v1 shared-view read surface).

The shared view is resolved from the ``view`` row by shareId (enableShare).
Read endpoints reuse the field/record/aggregation services scoped to that
view. copy delegates to the selection clipboard engine; collaborators derive
from the view's user-related fields; button-click bumps the button cell count.
"""

from typing import Any

from ...core import cls
from ...core.errors import ApiError, HttpErrorCode
from ...core.security.auth import JwtService
from ...core.security.constants import ANONYMOUS_USER
from ..aggregation.service import AggregationService
from ..field.service import FieldService
from ..record import repository as record_repository
from ..record.service import RecordService
from . import repository


def _share_view_not_found() -> ApiError:
    return ApiError(
        "Share view not found",
        HttpErrorCode.VALIDATION_ERROR,
        {"localization": {"i18nKey": "httpErrors.shareAuth.shareViewNotFound"}},
    )


def _unauthorized() -> ApiError:
    return ApiError("Unauthorized", HttpErrorCode.UNAUTHORIZED_SHARE)


def _link_title_str(value: Any) -> Any:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return str(int(value)) if float(value).is_integer() else str(value)
    return str(value)


def _is_not_hidden_field(field_id: str, view: dict[str, Any]) -> bool:
    view_type = view.get("type")
    column_meta = view.get("columnMeta") or {}
    options = view.get("options") or {}
    meta = column_meta.get(field_id) or {}
    if view_type == "kanban":
        return field_id in (options.get("stackFieldId"), options.get("coverFieldId")) or (
            meta.get("visible") is not False
        )
    if view_type == "gallery":
        return field_id == options.get("coverFieldId") or meta.get("visible") is not False
    if view_type == "calendar":
        color = options.get("colorConfig") or {}
        return (
            (color.get("type") == "field" and color.get("fieldId") == field_id)
            or field_id
            in (
                options.get("startDateFieldId"),
                options.get("endDateFieldId"),
                options.get("titleFieldId"),
            )
            or meta.get("visible") is not False
        )
    if view_type == "form":
        return bool(meta.get("visible"))
    return not meta.get("hidden")


class ShareService:
    async def resolve_share_info(
        self, share_id: str, cookie_token: str | None
    ) -> dict[str, Any]:
        row = await repository.find_view_by_share_id(share_id)
        if row is None:
            raise _share_view_not_found()
        view = repository.view_vo(row)
        share_meta = view.get("shareMeta")
        password = (share_meta or {}).get("password")
        if password:
            if not cookie_token:
                raise _unauthorized()
            try:
                payload = JwtService().verify(cookie_token)
            except Exception as exc:
                raise _unauthorized() from exc
            if payload.get("shareId") != share_id or payload.get("password") != password:
                raise _unauthorized()
        return {
            "shareId": share_id,
            "tableId": row["table_id"],
            "view": view,
            "shareMeta": share_meta,
        }

    # -- auth ------------------------------------------------------------------
    async def auth_share_view(self, share_id: str, password: str | None) -> str | None:
        row = await repository.find_view_by_share_id(share_id)
        if row is None:
            return None
        view = repository.view_vo(row)
        share_password = (view.get("shareMeta") or {}).get("password")
        if not share_password:
            raise ApiError(
                "Password restriction is not enabled",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.shareAuth.passwordRestrictionNotEnabled"}},
            )
        return share_id if password == share_password else None

    def auth_token(self, share_id: str, password: str) -> str:
        return JwtService().sign({"shareId": share_id, "password": password})

    # -- fields ----------------------------------------------------------------
    async def _share_fields(self, share_info: dict[str, Any]) -> list[dict[str, Any]]:
        view = share_info["view"]
        share_meta = share_info.get("shareMeta") or {}
        table_id = share_info["tableId"]
        fields = await FieldService().list_fields(table_id)
        filter_hidden = not share_meta.get("includeHiddenField")
        if filter_hidden:
            fields = [f for f in fields if _is_not_hidden_field(f["id"], view)]
        column_meta = view.get("columnMeta") or {}
        fields.sort(key=lambda f: (column_meta.get(f["id"]) or {}).get("order", 0))
        return fields

    def _precheck_field_hidden(self, share_info: dict[str, Any], field_id: str) -> None:
        if not (share_info.get("shareMeta") or {}).get("includeHiddenField") and (
            not _is_not_hidden_field(field_id, share_info["view"])
        ):
            raise ApiError(
                "field is hidden, not allowed",
                HttpErrorCode.RESTRICTED_RESOURCE,
                {"localization": {"i18nKey": "httpErrors.share.fieldHiddenNotAllowed"}},
            )

    async def _diff_ids_by_view_filter(
        self, share_info: dict[str, Any], ids: list[str]
    ) -> list[str]:
        if not ids:
            return []
        table_id = share_info["tableId"]
        record_service = RecordService()
        table, fields = await record_service._load_context(table_id)
        params: dict[str, Any] = {"__record_ids": list(ids)}
        compiled = record_service._compile_filter(
            fields, share_info["view"].get("filter"), params, [0]
        )
        clauses = ['"__id" = ANY(:__record_ids)']
        if compiled:
            clauses.append(f"({compiled})")
        rows = await record_repository.list_rows(
            table["base_id"],
            table_id,
            [],
            where_sql=" WHERE " + " AND ".join(clauses),
            params=params,
        )
        matched = {r["__id"] for r in rows}
        return [i for i in ids if i not in matched]

    # -- get share view --------------------------------------------------------
    async def get_share_view(self, share_info: dict[str, Any]) -> dict[str, Any]:
        share_id = share_info["shareId"]
        table_id = share_info["tableId"]
        view = share_info["view"]
        share_meta = share_info.get("shareMeta")
        fields = await self._share_fields(share_info)

        records: list[dict[str, Any]] = []
        extra: dict[str, Any] | None = None
        if (share_meta or {}).get("includeRecords"):
            data = await RecordService().list_records(
                table_id,
                field_key_type="id",
                view_id=view["id"],
                projection=[f["id"] for f in fields] or None,
                take=50,
                skip=0,
            )
            records = data["records"]
            extra = data.get("extra")

        result: dict[str, Any] = {
            "tableId": table_id,
            "shareId": share_id,
            "fields": fields,
            "records": records,
            "viewId": view["id"],
            "view": view,
        }
        if share_meta is not None:
            result["shareMeta"] = share_meta
        if extra:
            result["extra"] = extra
        return result

    # -- link records (candidate/selected foreign records for a link field) ----
    async def get_view_link_records(
        self, share_info: dict[str, Any], query: dict[str, Any]
    ) -> list[dict[str, Any]]:
        import json

        from sqlalchemy import text

        from ...db import engine as db_engine
        from ...db.provider import parse_db_table_name
        from ..field import repository as field_repository
        from ..field.repository import get_table_meta_by_id
        from ..table import repository as table_repository

        view = share_info.get("view")
        if not view:
            raise ApiError(
                "view is required",
                HttpErrorCode.NOT_FOUND,
                {"localization": {"i18nKey": "httpErrors.share.viewRequired"}},
            )
        field_id = query["fieldId"]
        if not (share_info.get("shareMeta") or {}).get("includeHiddenField") and (
            not _is_not_hidden_field(field_id, view)
        ):
            raise ApiError(
                "field is hidden, not allowed",
                HttpErrorCode.RESTRICTED_RESOURCE,
                {"localization": {"i18nKey": "httpErrors.share.fieldHiddenNotAllowed"}},
            )
        table_id = share_info["tableId"]
        field = await field_repository.get_field_row(table_id, field_id)
        if field is None:
            raise ApiError(
                f"Field {field_id} not found",
                HttpErrorCode.NOT_FOUND,
                {"localization": {"i18nKey": "httpErrors.field.notFound"}},
            )
        if field["type"] != "link":
            raise ApiError(
                "Field type is not link field",
                HttpErrorCode.RESTRICTED_RESOURCE,
                {"localization": {"i18nKey": "httpErrors.share.fieldTypeNotLinkField"}},
            )
        options = json.loads(field["options"] or "{}")
        foreign_table = await get_table_meta_by_id(options["foreignTableId"])
        foreign_fields = await table_repository.list_field_rows(options["foreignTableId"])
        lookup = next(
            (f for f in foreign_fields if f["id"] == options.get("lookupFieldId")), None
        ) or next((f for f in foreign_fields if f.get("is_primary")), None)
        if foreign_table is None or lookup is None:
            return []

        b_schema, b_table = parse_db_table_name(foreign_table["db_table_name"])
        fk_schema, fk_table = parse_db_table_name(options["fkHostTableName"])
        title_col = lookup["db_field_name"]
        params: dict[str, Any] = {}
        if (fk_schema, fk_table) != (b_schema, b_table):
            fk = options["foreignKeyName"]
            selected = (
                f'b."__id" IN (SELECT "{fk}" FROM "{fk_schema}"."{fk_table}" '
                f'WHERE "{fk}" IS NOT NULL)'
            )
        else:
            selected = f'b."{options["selfKeyName"]}" IS NOT NULL'
        clauses = [selected]
        search = query.get("search")
        if search:
            clauses.append(f'b."{title_col}"::text ILIKE :search')
            params["search"] = f"%{search}%"
        sql = (
            f'SELECT b."__id" AS id, b."{title_col}" AS title '
            f'FROM "{b_schema}"."{b_table}" b WHERE {" AND ".join(clauses)} '
            'ORDER BY b."__auto_number"'
        )
        take = query.get("take")
        if take is not None:
            sql += " LIMIT :take OFFSET :skip"
            params["take"] = int(take)
            params["skip"] = int(query.get("skip") or 0)
        async with db_engine.session() as session:
            rows = (await session.execute(text(sql), params)).mappings().all()
        return [
            {"id": r["id"], "title": _link_title_str(r["title"])} for r in rows
        ]

    # -- records ---------------------------------------------------------------
    async def get_view_records(
        self, share_info: dict[str, Any], query: dict[str, Any]
    ) -> dict[str, Any]:
        share_meta = share_info.get("shareMeta") or {}
        if not share_meta.get("includeRecords"):
            return {"records": []}
        view = share_info["view"]
        fields = await self._share_fields(share_info)
        all_field_ids = [f["id"] for f in fields]
        requested = query.get("projection")
        if requested:
            allowed = set(all_field_ids)
            projection = [p for p in requested if p in allowed]
            if not projection:
                projection = all_field_ids
        else:
            projection = all_field_ids
        return await RecordService().list_records(
            share_info["tableId"],
            field_key_type="id",
            view_id=view["id"],
            projection=projection,
            filter_param=query.get("filter"),
            order_by=query.get("orderBy"),
            search=query.get("search"),
            take=query.get("take") or 100,
            skip=query.get("skip") or 0,
        )

    # -- aggregations ----------------------------------------------------------
    async def get_view_aggregations(
        self, share_info: dict[str, Any], field_stats, filter_param
    ) -> dict[str, Any]:
        if not (share_info.get("shareMeta") or {}).get("includeRecords"):
            return {"aggregations": []}
        for _func, field_ids in field_stats or []:
            for field_id in field_ids:
                self._precheck_field_hidden(share_info, field_id)
        return await AggregationService().get_aggregation(
            share_info["tableId"],
            field_stats=field_stats,
            filter_param=filter_param,
            tql=None,
            search=None,
            view_id=share_info["view"]["id"],
            group_by=None,
            ignore_view_query=False,
        )

    async def get_view_row_count(
        self, share_info: dict[str, Any], filter_param
    ) -> dict[str, Any]:
        if not (share_info.get("shareMeta") or {}).get("includeRecords"):
            return {"rowCount": 0}
        return await AggregationService().get_row_count(
            share_info["tableId"],
            filter_param=filter_param,
            tql=None,
            search=None,
            view_id=share_info["view"]["id"],
            selected_record_ids=None,
            ignore_view_query=False,
        )

    async def get_view_group_points(
        self, share_info: dict[str, Any], filter_param, group_by, collapsed_ids
    ) -> Any:
        if not (share_info.get("shareMeta") or {}).get("includeRecords"):
            return []
        for item in group_by or []:
            self._precheck_field_hidden(share_info, item.get("fieldId", ""))
        return await AggregationService().get_group_points(
            share_info["tableId"],
            filter_param=filter_param,
            tql=None,
            search=None,
            group_by=group_by,
            collapsed_ids=collapsed_ids,
            view_id=share_info["view"]["id"],
            ignore_view_query=False,
        )

    async def get_search_count(
        self, share_info: dict[str, Any], filter_param, search
    ) -> dict[str, Any]:
        return await AggregationService().get_search_count(
            share_info["tableId"],
            filter_param=filter_param,
            search=search,
            view_id=share_info["view"]["id"],
            ignore_view_query=False,
        )

    async def get_search_index(
        self, share_info: dict[str, Any], filter_param, search, take, skip, order_by, group_by
    ) -> Any:
        return await AggregationService().get_search_index(
            share_info["tableId"],
            filter_param=filter_param,
            search=search,
            take=take,
            skip=skip,
            order_by=order_by,
            group_by=group_by,
            view_id=share_info["view"]["id"],
            ignore_view_query=False,
        )

    async def get_calendar_daily_collection(
        self, share_info: dict[str, Any], params: dict[str, Any]
    ) -> dict[str, Any]:
        result = await AggregationService().get_calendar_daily_collection(
            share_info["tableId"],
            start_date=params["startDate"],
            end_date=params["endDate"],
            start_field_id=params["startDateFieldId"],
            end_field_id=params["endDateFieldId"],
            filter_param=params.get("filter"),
            search=params.get("search"),
            view_id=share_info["view"]["id"],
            ignore_view_query=False,
        )
        visible = {f["id"] for f in await self._share_fields(share_info)}
        return {
            **result,
            "records": [
                {
                    **record,
                    "fields": {
                        k: v
                        for k, v in (record.get("fields") or {}).items()
                        if k in visible
                    },
                }
                for record in result.get("records") or []
            ],
        }

    # -- form submit -----------------------------------------------------------
    async def form_submit(self, share_info: dict[str, Any], body) -> dict[str, Any]:
        view = share_info["view"]
        if not view:
            raise ApiError(
                "view is required",
                HttpErrorCode.RESTRICTED_RESOURCE,
                {"localization": {"i18nKey": "httpErrors.share.viewRequired"}},
            )
        if view.get("type") != "form":
            raise ApiError(
                "not allowed to submit",
                HttpErrorCode.RESTRICTED_RESOURCE,
                {"localization": {"i18nKey": "httpErrors.share.notAllowedToSubmit"}},
            )
        if not cls.get("user.id"):
            cls.set("user", dict(ANONYMOUS_USER))
        return await RecordService().form_submit(share_info["tableId"], body)

    # -- copy ------------------------------------------------------------------
    async def copy(
        self, share_info: dict[str, Any], query: dict[str, Any]
    ) -> dict[str, Any]:
        share_meta = share_info.get("shareMeta") or {}
        is_editor = bool(share_meta.get("allowEdit")) and bool(cls.get("user.id"))
        if not (share_meta.get("allowCopy") or is_editor):
            raise ApiError(
                "Not allowed to copy",
                HttpErrorCode.RESTRICTED_RESOURCE,
                {"localization": {"i18nKey": "httpErrors.share.notAllowedToCopy"}},
            )
        from ..selection.service import SelectionService

        scoped = {**query, "viewId": share_info["view"]["id"]}
        return await SelectionService().copy(share_info["tableId"], scoped)

    # -- button click ----------------------------------------------------------
    async def button_click(
        self, share_info: dict[str, Any], record_id: str, field_id: str
    ) -> dict[str, Any]:
        self._precheck_field_hidden(share_info, field_id)
        await self._valid_record_snapshot_permission(share_info, [record_id])
        if not cls.get("user.id"):
            cls.set("user", dict(ANONYMOUS_USER))
        return await RecordService().button_click(
            share_info["tableId"], record_id, field_id
        )

    # -- collaborators ---------------------------------------------------------
    async def get_view_collaborators(
        self, share_info: dict[str, Any], query: dict[str, Any]
    ) -> list[dict[str, Any]]:
        view = share_info.get("view")
        share_meta = share_info.get("shareMeta") or {}
        is_editor = bool(share_meta.get("allowEdit")) and bool(cls.get("user.id"))
        view_type = (view or {}).get("type")
        field_id = query.get("fieldId")
        if view is None or view_type in ("form", "kanban", "plugin") or is_editor:
            return await self._all_view_collaborators(share_info, query)
        if not field_id:
            raise ApiError(
                "fieldId is required",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.share.fieldIdRequired"}},
            )
        self._precheck_field_hidden(share_info, field_id)
        from ..field import repository as field_repository

        field = await field_repository.get_field_row(share_info["tableId"], field_id)
        if field is None:
            raise ApiError(
                f"Field {field_id} not found",
                HttpErrorCode.NOT_FOUND,
                {"localization": {"i18nKey": "httpErrors.field.notFound"}},
            )
        if field["type"] not in ("user", "createdBy", "lastModifiedBy"):
            raise ApiError(
                "field is not user related field",
                HttpErrorCode.RESTRICTED_RESOURCE,
                {"localization": {"i18nKey": "httpErrors.share.fieldNotUserRelatedField"}},
            )
        return await self._view_filter_collaborators(share_info, field, query)

    async def _all_view_collaborators(
        self, share_info: dict[str, Any], query: dict[str, Any]
    ) -> list[dict[str, Any]]:
        from ..collaborator.service import CollaboratorService

        table, _fields = await RecordService()._load_context(share_info["tableId"])
        users = await CollaboratorService().get_user_collaborators(
            table["base_id"],
            {
                "skip": query.get("skip") or 0,
                "take": query.get("take") if query.get("take") is not None else 50,
            },
        )
        search = (query.get("search") or "").strip().lower()
        result: list[dict[str, Any]] = []
        for user in users:
            if search and search not in (user.get("name") or "").lower():
                continue
            result.append(
                {
                    "userId": user["id"],
                    "userName": user["name"],
                    "avatar": user.get("avatar"),
                }
            )
        return result

    async def _view_filter_collaborators(
        self, share_info: dict[str, Any], field: dict[str, Any], query: dict[str, Any]
    ) -> list[dict[str, Any]]:
        from ...config import get_settings
        from ...core.storage import get_public_full_storage_url
        from ..space import repository as space_repository

        view = share_info["view"]
        field_id = field["id"]
        data = await RecordService().list_records(
            share_info["tableId"],
            field_key_type="id",
            view_id=view["id"],
            projection=[field_id],
            take=get_settings().max_read_rows,
            skip=0,
        )
        ordered_ids: list[str] = []
        seen: set[str] = set()
        for record in data["records"]:
            value = record["fields"].get(field_id)
            items = value if isinstance(value, list) else ([value] if value else [])
            for item in items:
                uid = item.get("id") if isinstance(item, dict) else None
                if uid and uid not in seen:
                    seen.add(uid)
                    ordered_ids.append(uid)
        rows = await space_repository.list_user_rows_by_ids(ordered_ids)
        by_id = {u["id"]: u for u in rows}
        collaborators = [by_id[i] for i in ordered_ids if i in by_id]
        search = (query.get("search") or "").strip().lower()
        if search:
            collaborators = [
                u for u in collaborators if search in (u.get("name") or "").lower()
            ]
        skip = query.get("skip") or 0
        take = query.get("take") if query.get("take") is not None else 50
        paged = collaborators[skip : skip + take]
        return [
            {
                "userId": u["id"],
                "userName": u["name"],
                "email": u.get("email"),
                "avatar": get_public_full_storage_url(u["avatar"])
                if u.get("avatar")
                else None,
            }
            for u in paged
        ]

    # -- socket ----------------------------------------------------------------
    async def get_view_snapshot_bulk(
        self, share_info: dict[str, Any], ids: list[str], single: bool
    ) -> list[dict[str, Any]]:
        from ..view.service import ViewService

        view = share_info["view"]
        if not view:
            raise ApiError(
                "View not found",
                HttpErrorCode.NOT_FOUND,
                {"localization": {"i18nKey": "httpErrors.view.notFound"}},
            )
        # Upstream reads ids via @Query('ids'): a single ?ids= value arrives as a
        # string, so ids.length is the string length (>1) and the guard rejects
        # it. Only the ids[]=/repeated-array form with exactly [view.id] passes.
        view_id = view["id"]
        denied = False
        if single:
            value = ids[0] if ids else ""
            if len(value) > 1 or value != view_id:
                denied = True
        elif len(ids) > 1 or (ids and ids[0] != view_id):
            denied = True
        if denied:
            raise ApiError(
                "View permission not allowed: read",
                HttpErrorCode.RESTRICTED_RESOURCE,
                {"localization": {"i18nKey": "httpErrors.shareSocket.viewPermissionNotAllowed"}},
            )
        return await ViewService().socket_snapshot_bulk(share_info["tableId"], [view_id])

    async def get_view_doc_ids(self, share_info: dict[str, Any]) -> dict[str, Any]:
        return {"ids": [share_info["view"]["id"]]}

    async def get_field_snapshot_bulk(
        self, share_info: dict[str, Any], ids: list[str]
    ) -> list[dict[str, Any]]:
        # Upstream share path returns snapshots for every visible field, and its
        # VO always carries isMultipleCellValue (even false), unlike the plain
        # field socket VO which omits it when false.
        field_ids = [f["id"] for f in await self._share_fields(share_info)]
        requested = ids if isinstance(ids, list) else [ids]
        unpermitted = [i for i in requested if i not in field_ids]
        if unpermitted:
            raise ApiError(
                f"Field({','.join(unpermitted)}) permission not allowed: read",
                HttpErrorCode.RESTRICTED_RESOURCE,
                {"localization": {"i18nKey": "httpErrors.shareSocket.fieldPermissionNotAllowed"}},
            )
        snapshots = await FieldService().socket_snapshot_bulk(
            share_info["tableId"], field_ids
        )
        for snap in snapshots:
            snap["data"].setdefault("isMultipleCellValue", False)
        return snapshots

    async def get_field_doc_ids(self, share_info: dict[str, Any]) -> dict[str, Any]:
        return {"ids": [f["id"] for f in await self._share_fields(share_info)]}

    async def get_record_snapshot_bulk(
        self, share_info: dict[str, Any], ids: list[str], projection
    ) -> list[dict[str, Any]]:
        await self._valid_record_snapshot_permission(share_info, ids)
        allowed = [f["id"] for f in await self._share_fields(share_info)]
        allowed_set = set(allowed)
        requested = (
            [fid for fid, included in projection.items() if included]
            if isinstance(projection, dict)
            else []
        )
        projected = (
            [fid for fid in requested if fid in allowed_set] if requested else allowed
        )
        safe_projection = dict.fromkeys(projected, True)
        return await RecordService().socket_snapshot_bulk(
            share_info["tableId"], ids, safe_projection
        )

    async def _valid_record_snapshot_permission(
        self, share_info: dict[str, Any], ids: list[str]
    ) -> None:
        if not (share_info.get("shareMeta") or {}).get("includeRecords"):
            raise ApiError(
                f"Record({','.join(ids)}) permission not allowed: read",
                HttpErrorCode.RESTRICTED_RESOURCE,
                {"localization": {"i18nKey": "httpErrors.shareSocket.recordPermissionNotAllowed"}},
            )
        diff = await self._diff_ids_by_view_filter(share_info, ids)
        if diff:
            raise ApiError(
                f"Record({','.join(diff)}) permission not allowed: read",
                HttpErrorCode.RESTRICTED_RESOURCE,
                {"localization": {"i18nKey": "httpErrors.shareSocket.recordPermissionNotAllowed"}},
            )

    async def get_record_doc_ids(
        self, share_info: dict[str, Any], query: dict[str, Any]
    ) -> dict[str, Any]:
        if not (share_info.get("shareMeta") or {}).get("includeRecords"):
            return {"ids": []}
        scoped = {**query, "viewId": share_info["view"]["id"]}
        return await RecordService().socket_doc_ids(share_info["tableId"], scoped)
