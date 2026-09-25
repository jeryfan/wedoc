"""Base .tea export / import.

Package a base (a ``structure.json`` manifest mirroring the reference
generateBaseStructConfig shape + per-table record data) into a zip, and rebuild
it into a new base with full id remapping. The zip is not byte-comparable across
backends, so parity is validated by round-trip (export -> import -> data
consistent) plus manifest-structure comparison against the reference package.

Link relationships are re-established after every table exists (the owning side
is created via the field service, its symmetric twin is renamed to preserve the
exported name); lookup/rollup fields are rebuilt last, then record cells are
inserted scalar-first and link cells patched with remapped foreign record ids.
"""

import io
import json
import math
import time
import zipfile
from types import SimpleNamespace
from typing import Any

from ...config import get_settings
from ...core.errors import ApiError, HttpErrorCode
from ...core.ids import random_string
from ...core.storage import READ_PATH, get_storage, path_join, storage_token_encryptor
from ..attachment.schemas import UploadType, upload_dir
from ..table import repository as table_repository
from . import repository

STRUCTURE_NAME = "structure.json"
FILE_SUFFIX = "tea"
_EXPORT_VERSION = "1.0.0"


def _private_bucket() -> str:
    return get_settings().backend_storage_private_bucket


def _preview_url(bucket: str, path: str, file_name: str) -> str:
    settings = get_settings()
    disposition = f"attachment; filename*=UTF-8''{path_join(file_name)}"
    expires = math.floor(time.time()) + 60 * 60 * 24
    payload = {
        "expiresDate": expires,
        "respHeaders": {
            "Content-Type": "application/octet-stream",
            "Content-Disposition": disposition,
        },
    }
    token = storage_token_encryptor(settings).encrypt(payload)
    return f"{path_join(READ_PATH, bucket, path)}?token={token}"


def _view_config(v: dict[str, Any]) -> dict[str, Any]:
    def _p(raw: Any) -> Any:
        return json.loads(raw) if isinstance(raw, str) else raw

    return {
        "id": v.get("id"),
        "name": v.get("name"),
        "description": v.get("description"),
        "type": v.get("type"),
        "sort": _p(v.get("sort")),
        "filter": _p(v.get("filter")),
        "group": _p(v.get("group")),
        "options": _p(v.get("options")),
        "columnMeta": _p(v.get("column_meta")),
        "enableShare": v.get("enable_share"),
        "shareMeta": _p(v.get("share_meta")),
        "shareId": v.get("share_id"),
        "isLocked": v.get("is_locked"),
        "order": v.get("order"),
    }


def _field_kind(field: dict[str, Any]) -> str:
    if field.get("isLookup"):
        return "lookup"
    if field.get("type") == "link":
        return "link"
    if field.get("type") == "rollup":
        return "rollup"
    return "scalar"


async def export_base(base_id: str, include_data: bool) -> dict[str, Any]:
    from ..field.service import FieldService
    from ..record.service import RecordService
    from ..table.service import TableService

    base = await repository.get_base_row(base_id)
    if base is None or base["deleted_time"] is not None:
        raise ApiError(
            "Base not found",
            HttpErrorCode.NOT_FOUND,
            {"localization": {"i18nKey": "httpErrors.base.notFound"}},
        )
    table_service = TableService()
    field_service = FieldService()
    record_service = RecordService()

    tables = await table_service.list_tables(base_id)
    structure_tables: list[dict[str, Any]] = []
    data: dict[str, list[dict[str, Any]]] = {}
    for table in tables:
        fields = await field_service.list_fields(table["id"])
        view_rows = await table_repository.list_view_rows(table["id"])
        table_obj: dict[str, Any] = {
            "id": table["id"],
            "name": table["name"],
            "order": table.get("order"),
        }
        # match generateBaseStructConfig + JSON.stringify: absent description /
        # icon are dropped rather than serialized as null.
        if table.get("description") is not None:
            table_obj["description"] = table["description"]
        if table.get("icon") is not None:
            table_obj["icon"] = table["icon"]
        table_obj["dbTableName"] = (table.get("dbTableName") or "").split(".")[-1] or None
        table_obj["fields"] = fields
        table_obj["views"] = [_view_config(v) for v in view_rows]
        structure_tables.append(table_obj)
        if include_data:
            records = await record_service.list_records(
                table["id"], field_key_type="id", take=100000
            )
            data[table["id"]] = [
                {"id": r["id"], "fields": r["fields"]} for r in records["records"]
            ]

    structure = {
        "id": base["id"],
        "name": base["name"],
        "icon": base.get("icon"),
        "version": _EXPORT_VERSION,
        "tables": structure_tables,
        "plugins": [],
        "folders": [],
        "nodes": [],
    }

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(STRUCTURE_NAME, json.dumps(structure, ensure_ascii=False, indent=2))
        for table_id, records in data.items():
            zf.writestr(
                f"tables/{table_id}.json", json.dumps(records, ensure_ascii=False)
            )
    payload = buffer.getvalue()

    bucket = _private_bucket()
    token = random_string(24)
    path = f"{upload_dir(UploadType.EXPORT_BASE)}/{token}.{FILE_SUFFIX}"
    get_storage().upload_file(bucket, path, payload)
    file_name = f"{base['name']}.{FILE_SUFFIX}"
    return {
        "previewUrl": _preview_url(bucket, path, file_name),
        "baseName": base["name"],
        "fileName": file_name,
    }


def _read_package(notify: dict[str, Any]) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    path = notify.get("path")
    if not path:
        raise ApiError("Import file path is required", HttpErrorCode.VALIDATION_ERROR)
    payload = get_storage().read_file(_private_bucket(), path)
    if payload is None:
        raise ApiError(
            "Import file not found",
            HttpErrorCode.VALIDATION_ERROR,
            {"localization": {"i18nKey": "httpErrors.attachment.notFound"}},
        )
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        structure = json.loads(zf.read(STRUCTURE_NAME))
        data: dict[str, list[dict[str, Any]]] = {}
        for name in zf.namelist():
            if name.startswith("tables/") and name.endswith(".json"):
                table_id = name[len("tables/") : -len(".json")]
                data[table_id] = json.loads(zf.read(name))
    return structure, data


async def import_base(notify: dict[str, Any], space_id: str) -> dict[str, Any]:
    from ..field.service import FieldService
    from ..record.service import RecordService
    from ..table.service import TableService
    from .service import BaseService

    structure, data = _read_package(notify)
    field_service = FieldService()
    record_service = RecordService()
    table_service = TableService()

    new_base = await BaseService().create_base(
        space_id, structure.get("name"), structure.get("icon")
    )
    new_base_id = new_base["id"]

    table_id_map: dict[str, str] = {}
    field_id_map: dict[str, str] = {}
    view_id_map: dict[str, str] = {}
    tables = structure.get("tables") or []

    # Pass A: create every table with its scalar fields + views (link/lookup/
    # rollup are added once all tables exist so foreign references resolve).
    for table in tables:
        fields = table.get("fields") or []
        scalar = [f for f in fields if _field_kind(f) == "scalar"]
        scalar.sort(key=lambda f: 0 if f.get("isPrimary") else 1)
        field_ros = [_scalar_field_ro(f) for f in scalar]
        view_ros = [
            {"name": v.get("name"), "type": v.get("type")} for v in table.get("views") or []
        ]
        body = SimpleNamespace(
            name=table["name"],
            fields=field_ros or None,
            views=view_ros or None,
            records=[],
            fieldKeyType=None,
            dbTableName=None,
        )
        created = await table_service.create_table(new_base_id, body)
        table_id_map[table["id"]] = created["id"]
        new_fields = await field_service.list_fields(created["id"])
        by_db_name = {f["dbFieldName"]: f["id"] for f in new_fields}
        for f in scalar:
            new_fid = by_db_name.get(f["dbFieldName"])
            if new_fid:
                field_id_map[f["id"]] = new_fid
        new_views = await table_repository.list_view_rows(created["id"])
        _map_views(table.get("views") or [], new_views, view_id_map)
    return await _finish_import(
        structure,
        data,
        new_base,
        table_id_map,
        field_id_map,
        view_id_map,
        field_service,
        record_service,
    )


def _scalar_field_ro(field: dict[str, Any]) -> dict[str, Any]:
    ro: dict[str, Any] = {"name": field["name"], "type": field["type"]}
    if field.get("dbFieldName"):
        ro["dbFieldName"] = field["dbFieldName"]
    if field.get("options") is not None:
        ro["options"] = field["options"]
    if field.get("isPrimary"):
        ro["isPrimary"] = True
    if field.get("unique"):
        ro["unique"] = True
    if field.get("notNull"):
        ro["notNull"] = True
    return ro


def _map_views(
    struct_views: list[dict[str, Any]], new_views: list[dict[str, Any]], view_id_map: dict[str, str]
) -> None:
    used: set[str] = set()
    for sv in struct_views:
        match = next(
            (v for v in new_views if v["name"] == sv.get("name") and v["id"] not in used), None
        )
        if match is None:
            match = next((v for v in new_views if v["id"] not in used), None)
        if match is not None:
            used.add(match["id"])
            if sv.get("id"):
                view_id_map[sv["id"]] = match["id"]


def _find_field(tables: list[dict[str, Any]], field_id: str) -> dict[str, Any] | None:
    for table in tables:
        for field in table.get("fields") or []:
            if field.get("id") == field_id:
                return field
    return None


def _remap_filter(node: Any, field_id_map: dict[str, str]) -> Any:
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key == "fieldId" and isinstance(value, str):
                out[key] = field_id_map.get(value, value)
            else:
                out[key] = _remap_filter(value, field_id_map)
        return out
    if isinstance(node, list):
        return [_remap_filter(item, field_id_map) for item in node]
    return node


def _remap_link_cell(value: Any, record_id_map: dict[str, str]) -> Any:
    if isinstance(value, list):
        out = []
        for item in value:
            if isinstance(item, dict) and item.get("id") in record_id_map:
                out.append({"id": record_id_map[item["id"]]})
        return out or None
    if isinstance(value, dict) and value.get("id") in record_id_map:
        return {"id": record_id_map[value["id"]]}
    return None


async def _finish_import(
    structure: dict[str, Any],
    data: dict[str, list[dict[str, Any]]],
    new_base: dict[str, Any],
    table_id_map: dict[str, str],
    field_id_map: dict[str, str],
    view_id_map: dict[str, str],
    field_service: Any,
    record_service: Any,
) -> dict[str, Any]:
    from ..field.schemas import FieldCreateBody, FieldPatchBody
    from ..record.schemas import RecordCreateBody, RecordPatchBody

    tables = structure.get("tables") or []

    # Pass B: link fields. Create the owning side; its auto-generated symmetric
    # twin is mapped + renamed to preserve the exported name (cross-base links,
    # whose foreign table is not in this package, are skipped).
    handled: set[str] = set()
    created_link_old_ids: set[str] = set()
    for table in tables:
        new_tid = table_id_map[table["id"]]
        for field in table.get("fields") or []:
            if _field_kind(field) != "link" or field["id"] in handled:
                continue
            opts = field.get("options") or {}
            foreign_new = table_id_map.get(opts.get("foreignTableId"))
            if not foreign_new:
                continue
            link_opts: dict[str, Any] = {
                "relationship": opts.get("relationship"),
                "foreignTableId": foreign_new,
            }
            if opts.get("isOneWay"):
                link_opts["isOneWay"] = True
            body = FieldCreateBody.zod_validate(
                {
                    "name": field["name"],
                    "type": "link",
                    "dbFieldName": field.get("dbFieldName"),
                    "options": link_opts,
                }
            )
            vo = await field_service.create_field(new_tid, body)
            field_id_map[field["id"]] = vo["id"]
            created_link_old_ids.add(field["id"])
            sym_old = opts.get("symmetricFieldId")
            sym_new = (vo.get("options") or {}).get("symmetricFieldId")
            if sym_old and sym_new:
                field_id_map[sym_old] = sym_new
                handled.add(sym_old)
                sym_struct = _find_field(tables, sym_old)
                if sym_struct and sym_struct.get("name") != vo.get("options", {}).get("name"):
                    await field_service.update_field(
                        foreign_new, sym_new, FieldPatchBody.zod_validate(
                            {"name": sym_struct["name"]}
                        )
                    )

    # Pass C: lookup + rollup fields (all links now exist).
    for table in tables:
        new_tid = table_id_map[table["id"]]
        for field in table.get("fields") or []:
            kind = _field_kind(field)
            if kind not in ("lookup", "rollup"):
                continue
            lo = field.get("lookupOptions") or {}
            link_new = field_id_map.get(lo.get("linkFieldId"))
            foreign_new = table_id_map.get(lo.get("foreignTableId"))
            lookup_new = field_id_map.get(lo.get("lookupFieldId"))
            if not (link_new and foreign_new and lookup_new):
                continue
            new_lo: dict[str, Any] = {
                "linkFieldId": link_new,
                "lookupFieldId": lookup_new,
                "foreignTableId": foreign_new,
            }
            if lo.get("filter"):
                new_lo["filter"] = _remap_filter(lo["filter"], field_id_map)
            ro: dict[str, Any] = {
                "name": field["name"],
                "type": field["type"],
                "dbFieldName": field.get("dbFieldName"),
                "lookupOptions": new_lo,
                "options": field.get("options"),
            }
            if kind == "lookup":
                ro["isLookup"] = True
            vo = await field_service.create_field(new_tid, FieldCreateBody.zod_validate(ro))
            field_id_map[field["id"]] = vo["id"]

    # Pass D: records (scalar cells only), capturing old -> new record ids.
    record_id_map: dict[str, str] = {}
    for table in tables:
        new_tid = table_id_map[table["id"]]
        kinds = {f["id"]: _field_kind(f) for f in table.get("fields") or []}
        rows = data.get(table["id"]) or []
        record_ros = []
        for row in rows:
            fields = {}
            for old_fid, value in (row.get("fields") or {}).items():
                if kinds.get(old_fid) != "scalar":
                    continue
                new_fid = field_id_map.get(old_fid)
                if new_fid is not None:
                    fields[new_fid] = value
            record_ros.append({"fields": fields})
        if not record_ros:
            continue
        created = await record_service.create_records(
            new_tid,
            RecordCreateBody.zod_validate({"records": record_ros, "fieldKeyType": "id"}),
        )
        for old_row, new_row in zip(rows, created["records"], strict=False):
            record_id_map[old_row["id"]] = new_row["id"]

    # Pass E: link cells (owning side only), remapping foreign record ids.
    for table in tables:
        new_tid = table_id_map[table["id"]]
        link_fields = [
            f
            for f in table.get("fields") or []
            if f["id"] in created_link_old_ids
        ]
        if not link_fields:
            continue
        for row in data.get(table["id"]) or []:
            new_rid = record_id_map.get(row["id"])
            if not new_rid:
                continue
            cell_updates: dict[str, Any] = {}
            for field in link_fields:
                value = (row.get("fields") or {}).get(field["id"])
                if value is None:
                    continue
                new_fid = field_id_map.get(field["id"])
                remapped = _remap_link_cell(value, record_id_map)
                if new_fid and remapped is not None:
                    cell_updates[new_fid] = remapped
            if cell_updates:
                await record_service.update_record(
                    new_tid,
                    new_rid,
                    RecordPatchBody.zod_validate(
                        {"record": {"fields": cell_updates}, "fieldKeyType": "id"}
                    ),
                )

    return {
        "base": new_base,
        "tableIdMap": table_id_map,
        "fieldIdMap": field_id_map,
        "viewIdMap": view_id_map,
    }
