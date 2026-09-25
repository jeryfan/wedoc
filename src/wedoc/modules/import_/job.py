"""Import chunk job — ports the record-insertion path of import-csv*.processor.

Registered as the ``import_csv_chunk`` queue task. Parses the source file,
maps rows to record field values (with typecast coercion for the analyze
types), inserts them, and writes a result manifest so getImportStatus can read
success/failed counts back.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ...core import cls
from ...core.cache import get_cache
from ...workers.queue import task
from .. import import_ as _pkg  # noqa: F401
from ..record.schemas import RecordCreateBody, RecordItem
from ..record.service import RecordService
from . import importer

MANIFEST_TTL_SECONDS = 60 * 60


def result_manifest_key(job_id: str) -> str:
    return f"import:result:manifest:{job_id}"


def latest_job_key(table_id: str) -> str:
    return f"import:latest:job:{table_id}"


def _parse_boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.replace("'", "").replace('"', "").lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
    return bool(value)


def _coerce(field_type: str, value: Any) -> Any:
    if value is None or value == "":
        return None
    if field_type == "checkbox":
        return _parse_boolean(value)
    if field_type in ("number", "rating"):
        try:
            num = float(value)
        except (ValueError, TypeError):
            return None
        return int(num) if num.is_integer() else num
    if field_type in ("date", "createdTime", "lastModifiedTime"):
        iso = _to_iso(value)
        return iso
    if field_type == "link":
        return str(value)
    return str(value) if not isinstance(value, str) else value


def _to_iso(value: Any) -> str | None:
    s = str(value).strip()
    if not s:
        return None
    try:
        if len(s) == 10 and s[4] == "-":
            dt = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=UTC)
        else:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")
    except ValueError:
        return None


async def _load_sheet_rows(data: dict[str, Any]) -> list[list[Any]]:
    file_type = data["fileType"]
    sheet_key = data["sheetKey"]
    raw, _ = await importer.fetch_file(data["attachmentUrl"])
    if file_type == "csv":
        return importer.parse_csv_rows(raw, dynamic=False, limit=None)
    sheets = importer.parse_excel_sheets(raw)
    return sheets.get(sheet_key, [])


def _build_record_fields(data: dict[str, Any], row: list[Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    column_info = data.get("columnInfo")
    source_column_map = data.get("sourceColumnMap")
    field_defs = data["fields"]
    if column_info:
        for col_index, col in enumerate(column_info):
            src = col["sourceColumnIndex"]
            value = row[src] if isinstance(row, list) and src < len(row) else None
            fdef = field_defs[col_index]
            fields[fdef["id"]] = _coerce(fdef["type"], value)
    elif source_column_map:
        by_id = {f["id"]: f for f in field_defs}
        for field_id, src in source_column_map.items():
            if src is None:
                continue
            value = row[src] if isinstance(row, list) and src < len(row) else None
            ftype = by_id.get(field_id, {}).get("type", "singleLineText")
            fields[field_id] = _coerce(ftype, value)
    return fields


@task("import_csv_chunk")
async def import_csv_chunk(data: dict[str, Any]) -> dict[str, Any]:
    cls.set("user.id", data.get("userId"))
    table_id = data["tableId"]
    job_id = data["jobId"]

    rows = await _load_sheet_rows(data)
    if data.get("skipFirstNLines"):
        rows = rows[data["skipFirstNLines"]:]
    # drop wholly empty trailing rows the parser may emit
    rows = [r for r in rows if r is not None and any(str(c) != "" for c in r)]

    success = 0
    failed = 0
    service = RecordService()
    for row in rows:
        fields = _build_record_fields(data, row)
        body = RecordCreateBody.model_construct(
            records=[RecordItem.model_construct(fields=fields)],
            fieldKeyType="id",
            typecast=True,
        )
        try:
            await service.create_records(table_id, body)
            success += 1
        except Exception:
            failed += 1

    manifest = {"successCount": success, "failedCount": failed}
    await get_cache().set_detail(result_manifest_key(job_id), manifest, MANIFEST_TTL_SECONDS)
    return {"success": success, "failed": failed, "total": success + failed}
