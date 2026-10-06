"""selection domain service — ports selection.service.ts + the V2 paste family.

Scope: getIdsFromRanges, copy, clear, delete, the delete/clear/duplicate SSE
streams, and the paste family (paste / paste-by-id / temporaryPaste / copy-by-id
/ clear-by-id / delete-by-id and their streams). Paste ports the reference
clipboard cell coercion + overflow record/field/choice-creation engine
(typecast.py). Link/user/attachment cell coercion falls back to string values
(the link/collaborator engines belong to the B group).
"""

from typing import Any

from ...config import get_settings
from ...core import cls
from ...core.errors import ApiError, HttpErrorCode
from ..field.service import FieldService
from ..record.schemas import RecordBulkPatchBody, RecordCreateBody
from ..record.service import RecordService
from .clipboard import parse_clipboard_text, stringify_clipboard_text
from .typecast import FieldTypecaster


class RangeType:
    ROWS = "rows"
    COLUMNS = "columns"


class IdReturnType:
    RECORD_ID = "recordId"
    FIELD_ID = "fieldId"
    ALL = "all"


_SELECT_TYPES = ("singleSelect", "multipleSelect")


def _copy_header(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The clipboard header is the reference's field-instance serialization: it
    always carries isMultipleCellValue (the field VO omits it when false), and a
    select instance also exposes its (empty, lazily-filled) choice-lookup cache."""
    header: list[dict[str, Any]] = []
    for f in fields:
        item = {**f, "isMultipleCellValue": bool(f.get("isMultipleCellValue"))}
        if item.get("type") in _SELECT_TYPES:
            item["_innerChoicesMap"] = {}
            item["_innerChoicesMapKey"] = ""
        header.append(item)
    return header


class SelectionService:
    def __init__(self) -> None:
        self._settings = get_settings()

    async def _visible_fields(
        self, table_id: str, view_id: str | None, projection: list[str] | None
    ) -> list[dict[str, Any]]:
        fields = await FieldService().list_fields(table_id)
        view = None
        if view_id:
            from ..view.repository import get_view_row

            view = await get_view_row(table_id, view_id)
        if view is not None:
            column_meta = _json(view.get("column_meta"))
            options = _json(view.get("options"))
            view_type = view["type"]
            fields = [
                f
                for f in fields
                if _is_not_hidden(f["id"], view_type, column_meta, options)
            ]
            # fields absent from columnMeta (e.g. paste-created columns the view
            # hasn't registered) sort last in field creation order, matching the
            # reference which appends new fields to the view.
            fields.sort(
                key=lambda f: (column_meta.get(f["id"]) or {}).get("order", float("inf"))
            )
        if projection:
            by_id = {f["id"]: f for f in fields}
            fields = [by_id[fid] for fid in projection if fid in by_id]
        return fields

    async def _all_record_ids(
        self, table_id: str, query: dict[str, Any]
    ) -> list[str]:
        data = await RecordService().list_records(
            table_id,
            field_key_type="id",
            view_id=query.get("viewId"),
            filter_param=query.get("filter"),
            order_by=query.get("orderBy"),
            projection=[],
            take=self._settings.max_read_rows,
            skip=0,
        )
        return [r["id"] for r in data["records"]]

    async def _record_ids_in_range(
        self, table_id: str, query: dict[str, Any], ranges, range_type: str | None
    ) -> list[str]:
        all_ids = await self._all_record_ids(table_id, query)
        if range_type == RangeType.COLUMNS:
            return all_ids
        if range_type == RangeType.ROWS:
            ids: list[str] = []
            for start, end in ranges:
                ids.extend(all_ids[start : end + 1])
            return ids
        start, end = ranges[0], ranges[1]
        return all_ids[start[1] : end[1] + 1]

    async def _field_ids_in_range(
        self, table_id: str, query: dict[str, Any], ranges, range_type: str | None
    ) -> list[str]:
        fields = await self._visible_fields(
            table_id, query.get("viewId"), query.get("projection")
        )
        ids = [f["id"] for f in fields]
        if range_type == RangeType.ROWS:
            return ids
        if range_type == RangeType.COLUMNS:
            out: list[str] = []
            for start, end in ranges:
                out.extend(ids[start : end + 1])
            return out
        start, end = ranges[0], ranges[1]
        return ids[start[0] : end[0] + 1]

    # -- range-to-id -----------------------------------------------------------
    async def get_ids_from_ranges(
        self, table_id: str, query: dict[str, Any]
    ) -> dict[str, Any]:
        return_type = query.get("returnType")
        ranges = query["ranges"]
        range_type = query.get("type")
        if return_type == IdReturnType.RECORD_ID:
            return {
                "recordIds": await self._record_ids_in_range(
                    table_id, query, ranges, range_type
                )
            }
        if return_type == IdReturnType.FIELD_ID:
            return {
                "fieldIds": await self._field_ids_in_range(
                    table_id, query, ranges, range_type
                )
            }
        if return_type == IdReturnType.ALL:
            return {
                "fieldIds": await self._field_ids_in_range(
                    table_id, query, ranges, range_type
                ),
                "recordIds": await self._record_ids_in_range(
                    table_id, query, ranges, range_type
                ),
            }
        raise ApiError(
            "Invalid return type",
            HttpErrorCode.VALIDATION_ERROR,
            {"localization": {"i18nKey": "httpErrors.selection.invalidReturnType"}},
        )

    # -- selection context (records x fields) ----------------------------------
    async def _selection_ctx(
        self, table_id: str, query: dict[str, Any], ranges, range_type: str | None
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        all_fields = await self._visible_fields(
            table_id, query.get("viewId"), query.get("projection")
        )
        if range_type == RangeType.COLUMNS:
            selected_fields: list[dict[str, Any]] = []
            for start, end in ranges:
                selected_fields.extend(all_fields[start : end + 1])
            record_query = {**query, "skip": 0, "take": self._settings.max_read_rows}
        elif range_type == RangeType.ROWS:
            selected_fields = all_fields
            record_query = None  # handled per-range below
        else:
            start, end = ranges[0], ranges[1]
            selected_fields = all_fields[start[0] : end[0] + 1]
            record_query = {
                **query,
                "skip": start[1],
                "take": end[1] + 1 - start[1],
            }

        projection = [f["id"] for f in selected_fields]
        records: list[dict[str, Any]] = []
        if range_type == RangeType.ROWS:
            for start, end in ranges:
                data = await RecordService().list_records(
                    table_id,
                    field_key_type="id",
                    view_id=query.get("viewId"),
                    filter_param=query.get("filter"),
                    order_by=query.get("orderBy"),
                    projection=projection or [],
                    skip=start,
                    take=end + 1 - start,
                )
                records.extend(data["records"])
        else:
            data = await RecordService().list_records(
                table_id,
                field_key_type="id",
                view_id=record_query.get("viewId"),
                filter_param=record_query.get("filter"),
                order_by=record_query.get("orderBy"),
                projection=projection or [],
                skip=record_query["skip"],
                take=record_query["take"],
            )
            records = data["records"]
        return records, selected_fields

    async def _cell_count(
        self, table_id: str, query: dict[str, Any], ranges, range_type: str | None
    ) -> int:
        records, fields = await self._selection_ctx(table_id, query, ranges, range_type)
        return len(records) * len(fields)

    # -- copy ------------------------------------------------------------------
    async def copy(self, table_id: str, query: dict[str, Any]) -> dict[str, Any]:
        ranges = query["ranges"]
        range_type = query.get("type")
        records, fields = await self._selection_ctx(table_id, query, ranges, range_type)
        if len(records) * len(fields) > self._settings.max_copy_cells:
            raise ApiError(
                f"Exceed max copy cells {self._settings.max_copy_cells}",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.selection.exceedMaxCopyCells"}},
            )
        rectangle = [
            [_cv_to_string(f, r["fields"].get(f["id"])) for f in fields] for r in records
        ]
        return {"content": stringify_clipboard_text(rectangle), "header": _copy_header(fields)}

    # -- clear -----------------------------------------------------------------
    async def clear(
        self, table_id: str, body: dict[str, Any], window_id: str | None
    ) -> list[str]:
        ranges = body["ranges"]
        range_type = body.get("type")
        records, fields = await self._selection_ctx(table_id, body, ranges, range_type)
        field_ids = [f["id"] for f in fields]
        patch = RecordBulkPatchBody.zod_validate(
            {
                "fieldKeyType": "id",
                "records": [
                    {"id": r["id"], "fields": {fid: None for fid in field_ids}}
                    for r in records
                ],
            }
        )
        await RecordService().update_records(
            table_id, [r["id"] for r in records], patch
        )
        # cleared = records that actually had a non-null value in the selection.
        return [
            r["id"] for r in records if any(fid in r["fields"] for fid in field_ids)
        ]

    # -- delete ----------------------------------------------------------------
    async def delete(
        self, table_id: str, query: dict[str, Any], window_id: str | None
    ) -> dict[str, Any]:
        records, _ = await self._selection_ctx(
            table_id, query, query["ranges"], query.get("type")
        )
        record_ids = [r["id"] for r in records]
        await RecordService().delete_records(table_id, record_ids)
        return {"ids": record_ids}

    # -- duplicate (stream helper) ---------------------------------------------
    async def duplicate_record_ids(
        self, table_id: str, query: dict[str, Any]
    ) -> list[str]:
        records, _ = await self._selection_ctx(
            table_id, query, query["ranges"], query.get("type")
        )
        duplicated: list[str] = []
        service = RecordService()
        for record in records:
            dup = await service.duplicate_record(table_id, record["id"])
            duplicated.append(dup["id"])
        return duplicated

    # -- paste engine ----------------------------------------------------------
    async def _row_count_in_view(self, table_id: str, query: dict[str, Any]) -> int:
        from ..aggregation.service import AggregationService

        result = await AggregationService().get_row_count(
            table_id,
            filter_param=query.get("filter"),
            tql=None,
            search=None,
            view_id=query.get("viewId"),
            selected_record_ids=None,
            filter_link_cell_candidate=None,
            filter_link_cell_selected=None,
            projection=None,
            ignore_view_query=bool(query.get("ignoreViewQuery")),
        )
        return result["rowCount"]

    def _permissions(self) -> list[str]:
        return cls.get("permissions") or []

    async def _read_existing_records(
        self, table_id: str, query: dict[str, Any], projection: list[str], skip: int, take: int
    ) -> list[dict[str, Any]]:
        if take <= 0:
            return []
        data = await RecordService().list_records(
            table_id,
            field_key_type="id",
            view_id=query.get("viewId"),
            filter_param=query.get("filter"),
            order_by=query.get("orderBy"),
            ignore_view_query=bool(query.get("ignoreViewQuery")),
            projection=projection or [],
            skip=skip,
            take=take,
        )
        return [{"id": r["id"], "fields": r["fields"]} for r in data["records"]]

    async def _expand_columns(
        self, table_id: str, header: list[dict[str, Any]] | None, num_cols: int
    ) -> list[dict[str, Any]]:
        if num_cols <= 0:
            return []
        from ..field.schemas import FieldCreateBody

        header = header or []
        col_len = len(header)
        created: list[dict[str, Any]] = []
        service = FieldService()
        for i in range(col_len - num_cols, col_len):
            src = header[i] if 0 <= i < col_len else None
            ro = _field_vo_to_ro(src)
            body = FieldCreateBody.zod_validate(ro)
            field_vo = await service.create_field(table_id, body)
            created.append(field_vo)
        return created

    async def _typecast_records(
        self,
        table_id: str,
        fields: list[dict[str, Any]],
        records: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
        """Column-by-column typecast (ports validateFieldsAndTypecast). Mutates
        select fields with new choices; returns cast records + created choice ids."""
        out_fields: list[dict[str, Any]] = [{} for _ in records]
        created_choice_ids: dict[str, list[str]] = {}
        field_by_id = {f["id"]: f for f in fields}
        present_ids: list[str] = []
        for f in fields:
            if any(f["id"] in r["fields"] for r in records):
                present_ids.append(f["id"])
        for fid in present_ids:
            field = field_by_id[fid]
            if field.get("isComputed"):
                continue
            caster = FieldTypecaster(field)
            column = [r["fields"].get(fid) for r in records]
            cast = caster.cast(column)
            new_ids = await caster.flush_new_choices(table_id)
            if new_ids:
                created_choice_ids[fid] = new_ids
            for i, value in enumerate(cast):
                if value is not None or fid in records[i]["fields"]:
                    out_fields[i][fid] = value
        return [{"fields": ff} for ff in out_fields], created_choice_ids

    async def paste(
        self, table_id: str, body: dict[str, Any], window_id: str | None
    ) -> dict[str, Any]:
        content = body.get("content")
        header = body.get("header")
        ranges = body["ranges"]
        range_type = body.get("type")
        paste_content = parse_clipboard_text(content) if isinstance(content, str) else content
        paste_size = len(paste_content) * (len(paste_content[0]) if paste_content else 0)
        self._assert_max_paste(paste_size)

        fields = await self._visible_fields(
            table_id, body.get("viewId"), body.get("projection")
        )
        row_count = await self._row_count_in_view(table_id, body)
        table_size = (len(fields), row_count)
        range_cell = _get_range_cell(
            [[0, 0], [table_size[0] - 1, table_size[1] - 1]], ranges, range_type
        )
        table_data = _expand_paste_content(paste_content, range_cell)
        table_col_count = len(table_data[0]) if table_data else 0
        table_row_count = len(table_data)
        col, row = range_cell[0]

        effect_fields = fields[col : col + table_col_count]
        num_cols, num_rows = _calculate_expansion(
            table_size, range_cell[0], (table_col_count, table_row_count), self._permissions()
        )
        existing = await self._read_existing_records(
            table_id, body, [f["id"] for f in effect_fields], row, table_row_count
        )
        effect_ids = {f["id"] for f in effect_fields}
        cell_contexts: list[dict[str, Any]] = []
        new_record_vos: list[dict[str, Any]] = []
        created_record_ids: list[str] = []
        # paste is one user action: capture a single pasteSelection undo entry
        # instead of the create/update ops its sub-writes would each record.
        prev_suppress = cls.get("undoRedoSuppressCapture")
        cls.set("undoRedoSuppressCapture", True)
        try:
            new_fields = await self._expand_columns(table_id, header, num_cols)
            update_fields = effect_fields + new_fields
            records_from_clip = self._records_from_clipboard(table_data, update_fields, header)

            to_update = records_from_clip[: len(existing)]
            cast_updates, _ = await self._typecast_records(table_id, update_fields, to_update)
            if existing:
                for i, record in enumerate(existing):
                    old_fields = record["fields"]
                    for fid, new_value in cast_updates[i]["fields"].items():
                        if fid not in effect_ids:
                            continue
                        old_value = old_fields.get(fid)
                        if old_value != new_value:
                            cell_contexts.append(
                                {
                                    "recordId": record["id"],
                                    "fieldId": fid,
                                    "oldValue": old_value,
                                    "newValue": new_value,
                                }
                            )
                patch = RecordBulkPatchBody.zod_validate(
                    {
                        "fieldKeyType": "id",
                        "records": [
                            {"id": existing[i]["id"], "fields": cast_updates[i]["fields"]}
                            for i in range(len(existing))
                        ],
                    }
                )
                await RecordService().update_records(
                    table_id, [e["id"] for e in existing], patch
                )

            if num_rows:
                new_records = records_from_clip[len(existing) :]
                cast_creates, _ = await self._typecast_records(
                    table_id, update_fields, new_records
                )
                if cast_creates:
                    create_body = RecordCreateBody.zod_validate(
                        {
                            "fieldKeyType": "id",
                            "typecast": True,
                            "records": cast_creates,
                        }
                    )
                    result = await RecordService().create_records(table_id, create_body)
                    new_record_vos = result["records"]
                    created_record_ids = [r["id"] for r in new_record_vos]
        finally:
            cls.set("undoRedoSuppressCapture", prev_suppress)

        op_result: dict[str, Any] = {}
        if cell_contexts:
            op_result["updateRecords"] = {
                "recordIds": [e["id"] for e in existing],
                "fieldIds": sorted({c["fieldId"] for c in cell_contexts}),
                "cellContexts": cell_contexts,
            }
        if new_fields:
            op_result["newFields"] = new_fields
        if new_record_vos:
            op_result["newRecords"] = new_record_vos
        if op_result:
            from ..undo_redo.stack import capture_operation

            await capture_operation(
                table_id,
                {"name": "pasteSelection", "params": {"tableId": table_id}, "result": op_result},
            )

        update_range = [
            list(range_cell[0]),
            [col + len(update_fields) - 1, row + table_row_count - 1],
        ]
        return {
            "ranges": update_range,
            "updatedCount": len(existing),
            "createdCount": len(created_record_ids),
            "createdRecordIds": created_record_ids,
        }

    async def temporary_paste(
        self, table_id: str, body: dict[str, Any]
    ) -> list[dict[str, Any]]:
        content = body.get("content")
        header = body.get("header")
        ranges = body["ranges"]
        paste_content = parse_clipboard_text(content) if isinstance(content, str) else content
        paste_size = len(paste_content) * (len(paste_content[0]) if paste_content else 0)
        self._assert_max_paste(paste_size)

        fields = await self._visible_fields(
            table_id, body.get("viewId"), body.get("projection")
        )
        range_cell = ranges
        start_col = range_cell[0][0]
        table_data = _expand_paste_content(paste_content, range_cell)
        table_col_count = len(table_data[0]) if table_data else 0
        effect_fields = fields[start_col : start_col + table_col_count]
        records_from_clip = self._records_from_clipboard(table_data, effect_fields, header)
        cast_records, _ = await self._typecast_records(table_id, effect_fields, records_from_clip)
        return cast_records

    async def copy_by_id(self, table_id: str, body: dict[str, Any]) -> dict[str, Any]:
        record_ids = await self._resolve_record_ids(table_id, body)
        fields = await self._resolve_fields(table_id, body)
        if len(record_ids) * len(fields) > self._settings.max_copy_cells:
            raise self._max_copy_error()
        records = await self._records_by_ids(table_id, record_ids, [f["id"] for f in fields])
        rectangle = [
            [_cv_to_string(f, r["fields"].get(f["id"])) for f in fields] for r in records
        ]
        return {"content": stringify_clipboard_text(rectangle), "header": _copy_header(fields)}

    async def clear_by_id(
        self, table_id: str, body: dict[str, Any], window_id: str | None
    ) -> list[str]:
        record_ids = await self._resolve_record_ids(table_id, body)
        fields = await self._resolve_fields(table_id, body)
        return await self._clear_resolved(table_id, record_ids, fields)

    async def clear_by_ids(
        self, table_id: str, body: dict[str, Any], window_id: str | None
    ) -> list[str]:
        record_ids = await self._resolve_record_ids_from_ids(table_id, body)
        fields = await self._resolve_fields_from_ids(table_id, body)
        return await self._clear_resolved(table_id, record_ids, fields)

    async def _clear_resolved(
        self, table_id: str, record_ids: list[str], fields: list[dict[str, Any]]
    ) -> list[str]:
        field_ids = [f["id"] for f in fields]
        records = await self._records_by_ids(table_id, record_ids, field_ids)
        if not records:
            return []
        patch = RecordBulkPatchBody.zod_validate(
            {
                "fieldKeyType": "id",
                "records": [
                    {"id": r["id"], "fields": {fid: None for fid in field_ids}}
                    for r in records
                ],
            }
        )
        await RecordService().update_records(table_id, [r["id"] for r in records], patch)
        return [
            r["id"] for r in records if any(fid in r["fields"] for fid in field_ids)
        ]

    async def delete_by_id(
        self, table_id: str, body: dict[str, Any], window_id: str | None
    ) -> dict[str, Any]:
        record_ids = await self._resolve_record_ids(table_id, body)
        await RecordService().delete_records(table_id, record_ids)
        return {"ids": record_ids}

    async def delete_by_ids(
        self, table_id: str, body: dict[str, Any], window_id: str | None
    ) -> dict[str, Any]:
        record_ids = await self._resolve_record_ids_from_ids(table_id, body)
        await RecordService().delete_records(table_id, record_ids)
        return {"ids": record_ids}

    async def paste_by_id(
        self, table_id: str, body: dict[str, Any], window_id: str | None
    ) -> dict[str, Any]:
        content = body.get("content")
        header = body.get("header")
        record_ids = await self._resolve_record_ids(table_id, body)
        paste_content = parse_clipboard_text(content) if isinstance(content, str) else content
        fields = await self._resolve_fields(table_id, body)
        paste_size = len(paste_content) * (len(paste_content[0]) if paste_content else 0)
        self._assert_max_paste(paste_size)

        content_col_count = len(paste_content[0]) if paste_content else 0
        num_cols = max(0, content_col_count - len(fields))
        new_fields: list[dict[str, Any]] = []
        if num_cols and "field|create" in self._permissions():
            new_fields = await self._expand_columns(table_id, header, num_cols)
        fields = fields + new_fields
        field_ids = [f["id"] for f in fields]

        table_data = _expand_paste_content(
            paste_content,
            [[0, 0], [max(len(fields) - 1, 0), max(len(record_ids) - 1, 0)]],
        )
        records_from_clip = self._records_from_clipboard(table_data, fields, header)
        existing = await self._records_by_ids(table_id, record_ids, field_ids)

        to_update = records_from_clip[: len(existing)]
        before = self._choice_snapshot(fields)
        cast_updates, _ = await self._typecast_records(table_id, fields, to_update)
        if existing:
            patch = RecordBulkPatchBody.zod_validate(
                {
                    "fieldKeyType": "id",
                    "records": [
                        {"id": existing[i]["id"], "fields": cast_updates[i]["fields"]}
                        for i in range(len(existing))
                    ],
                }
            )
            await RecordService().update_records(
                table_id, [e["id"] for e in existing], patch
            )

        created_record_ids: list[str] = []
        new_records = records_from_clip[len(existing) :]
        if new_records:
            cast_creates, _ = await self._typecast_records(table_id, fields, new_records)
            create_body = RecordCreateBody.zod_validate(
                {"fieldKeyType": "id", "typecast": True, "records": cast_creates}
            )
            result = await RecordService().create_records(table_id, create_body)
            created_record_ids = [r["id"] for r in result["records"]]

        after = self._choice_snapshot(await self._resolve_fields(table_id, body))
        created_choices = _diff_choices(before, after)
        pasted_record_ids = [e["id"] for e in existing] + created_record_ids
        vo: dict[str, Any] = {
            "selection": {"recordIds": pasted_record_ids, "fieldIds": field_ids},
            "pastedRecordIds": pasted_record_ids,
            "pastedFieldIds": field_ids,
        }
        if created_record_ids:
            vo["createdRecordIds"] = created_record_ids
        if new_fields:
            vo["createdFieldIds"] = [f["id"] for f in new_fields]
        if created_choices:
            vo["createdChoiceIdsByFieldId"] = created_choices
        vo["skippedAttachments"] = []
        return {
            "vo": vo,
            "updatedCount": len(existing),
            "createdCount": len(created_record_ids),
            "createdRecordIds": created_record_ids,
        }

    # -- by-id / clipboard helpers ---------------------------------------------
    def _assert_max_paste(self, size: int) -> None:
        if size > self._settings.max_paste_cells:
            raise ApiError(
                f"Exceed max paste cells {self._settings.max_paste_cells}",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.selection.exceedMaxPasteCells"}},
            )

    def _max_copy_error(self) -> ApiError:
        return ApiError(
            f"Exceed max copy cells {self._settings.max_copy_cells}",
            HttpErrorCode.VALIDATION_ERROR,
            {"localization": {"i18nKey": "httpErrors.selection.exceedMaxCopyCells"}},
        )

    async def _resolve_record_ids(self, table_id: str, body: dict[str, Any]) -> list[str]:
        selection = body.get("selection") or {}
        if selection.get("recordIds") is not None:
            return selection["recordIds"]
        all_ids = await self._all_record_ids(table_id, body)
        excluded = set(selection.get("excludeRecordIds") or [])
        return [rid for rid in all_ids if rid not in excluded]

    async def _resolve_fields(
        self, table_id: str, body: dict[str, Any]
    ) -> list[dict[str, Any]]:
        selection = body.get("selection") or {}
        if selection.get("fieldIds"):
            all_fields = await FieldService().list_fields(table_id)
            by_id = {f["id"]: f for f in all_fields}
            return [by_id[fid] for fid in selection["fieldIds"] if fid in by_id]
        if body.get("projection"):
            all_fields = await FieldService().list_fields(table_id)
            by_id = {f["id"]: f for f in all_fields}
            return [by_id[fid] for fid in body["projection"] if fid in by_id]
        return await self._visible_fields(table_id, body.get("viewId"), None)

    async def _resolve_record_ids_from_ids(
        self, table_id: str, body: dict[str, Any]
    ) -> list[str]:
        selection = body.get("selection") or {}
        excluded = set(selection.get("excludedRecordIds") or [])
        if selection.get("allRecords"):
            all_ids = await self._all_record_ids(table_id, body)
            return [rid for rid in all_ids if rid not in excluded]
        record_ids = selection.get("recordIds") or []
        return [rid for rid in record_ids if rid not in excluded]

    async def _resolve_fields_from_ids(
        self, table_id: str, body: dict[str, Any]
    ) -> list[dict[str, Any]]:
        selection = body.get("selection") or {}
        field_ids = selection.get("fieldIds")
        if not selection.get("allFields") and field_ids:
            excluded = set(selection.get("excludedFieldIds") or [])
            all_fields = await FieldService().list_fields(table_id)
            by_id = {f["id"]: f for f in all_fields}
            return [
                by_id[fid] for fid in field_ids if fid in by_id and fid not in excluded
            ]
        return await self._visible_fields(
            table_id, body.get("viewId"), body.get("projection")
        )

    async def _records_by_ids(
        self, table_id: str, record_ids: list[str], field_ids: list[str]
    ) -> list[dict[str, Any]]:
        if not record_ids:
            return []
        data = await RecordService().list_records(
            table_id, field_key_type="id", projection=field_ids or [], take=len(record_ids) + 1000
        )
        by_id = {r["id"]: {"id": r["id"], "fields": r["fields"]} for r in data["records"]}
        return [by_id[rid] for rid in record_ids if rid in by_id]

    def _records_from_clipboard(
        self,
        table_data: list[list[Any]],
        fields: list[dict[str, Any]],
        header: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = [{"fields": {}} for _ in table_data]
        for col, field in enumerate(fields):
            if field.get("isComputed"):
                continue
            for row, cells in enumerate(table_data):
                records[row]["fields"][field["id"]] = (
                    cells[col] if cells and col < len(cells) else None
                )
        return records

    @staticmethod
    def _choice_snapshot(fields: list[dict[str, Any]]) -> dict[str, list[str]]:
        snap: dict[str, list[str]] = {}
        for f in fields:
            if f["type"] in ("singleSelect", "multipleSelect"):
                snap[f["id"]] = [
                    c["id"] for c in (f.get("options") or {}).get("choices") or []
                ]
        return snap


def _expand_paste_content(
    paste_data: list[list[Any]], range_cell: list[list[int]]
) -> list[list[Any]]:
    (start_col, start_row), (end_col, end_row) = range_cell[0], range_cell[1]
    range_rows = end_row - start_row + 1
    range_cols = end_col - start_col + 1
    paste_rows = len(paste_data)
    paste_cols = len(paste_data[0]) if paste_data else 0
    if paste_rows == 0 or paste_cols == 0:
        return paste_data
    if range_rows % paste_rows != 0 or range_cols % paste_cols != 0:
        return paste_data
    return [
        [paste_data[i % paste_rows][j % paste_cols] for j in range(range_cols)]
        for i in range(range_rows)
    ]


def _get_range_cell(
    max_range: list[list[int]], ranges: list[list[int]], range_type: str | None
) -> list[list[int]]:
    (max_start_col, max_start_row), (max_end_col, max_end_row) = max_range[0], max_range[1]
    if range_type == RangeType.COLUMNS:
        return [[ranges[0][0], max_start_row], [ranges[0][1], max_end_row]]
    if range_type == RangeType.ROWS:
        return [[max_start_col, ranges[0][0]], [max_end_col, ranges[0][1]]]
    return [ranges[0], ranges[1]]


def _calculate_expansion(
    table_size: tuple[int, int],
    cell: list[int],
    table_data_size: tuple[int, int],
    permissions: list[str],
) -> tuple[int, int]:
    num_cols, num_rows = table_size
    data_cols, data_rows = table_data_size
    end_col = cell[0] + data_cols
    end_row = cell[1] + data_rows
    cols_to_expand = max(0, end_col - num_cols)
    rows_to_expand = max(0, end_row - num_rows)
    return (
        cols_to_expand if "field|create" in permissions else 0,
        rows_to_expand if "record|create" in permissions else 0,
    )


_DEFAULT_FIELD_NAMES = {
    "singleLineText": "Label",
    "longText": "Notes",
    "number": "Number",
    "rating": "Rating",
    "singleSelect": "Select",
    "multipleSelect": "Tags",
    "attachment": "Attachments",
    "date": "Date",
    "checkbox": "Done",
    "button": "Button",
    "createdTime": "Created Time",
    "lastModifiedTime": "Last Modified Time",
}


def _field_vo_to_ro(field: dict[str, Any] | None) -> dict[str, Any]:
    if not field:
        return {"type": "singleLineText", "name": _DEFAULT_FIELD_NAMES["singleLineText"]}
    ftype = field.get("type", "singleLineText")
    is_computed = field.get("isComputed")
    is_lookup = field.get("isLookup")
    if is_computed and not is_lookup:
        ftype = "singleLineText"
    ro: dict[str, Any] = {"type": ftype}
    ro["name"] = field.get("name") or _DEFAULT_FIELD_NAMES.get(ftype, "Label")
    if not (is_computed and not is_lookup):
        if field.get("options") is not None:
            ro["options"] = field["options"]
        if field.get("description") is not None:
            ro["description"] = field["description"]
    return ro


def _cv_to_string(field: dict[str, Any], value: Any) -> str:
    from ..export.service import cell_value_to_string

    return cell_value_to_string(field, value)


def _diff_choices(
    before: dict[str, list[str]], after: dict[str, list[str]]
) -> dict[str, list[str]]:
    diff: dict[str, list[str]] = {}
    for field_id, ids in after.items():
        before_ids = set(before.get(field_id) or [])
        created = [cid for cid in ids if cid not in before_ids]
        if created:
            diff[field_id] = created
    return diff


def _json(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    import json

    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


def _is_not_hidden(
    field_id: str, view_type: str, column_meta: dict[str, Any], options: dict[str, Any]
) -> bool:
    meta = column_meta.get(field_id) or {}
    if view_type == "kanban":
        return field_id in (
            options.get("stackFieldId"),
            options.get("coverFieldId"),
        ) or meta.get("visible") is not False
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
