"""Import orchestration — ports import-open-api.service.ts (legacy V1).

analyze / createTableFromImport / inplaceImportTable / getImportStatus. Record
insertion runs through the queue (import_csv_chunk task); with the in-memory
fallback it executes inline so records land during the request.
"""

from __future__ import annotations

import secrets
from types import SimpleNamespace
from typing import Any

from ...core import cls
from ...core.cache import get_cache
from ...core.errors import ApiError, HttpErrorCode
from ...workers.queue import TaskQueue
from ..field.service import FieldService
from ..table.service import TableService
from . import importer
from .job import latest_job_key, result_manifest_key
from .schemas import AnalyzeRo, ImportOptionRo, InplaceImportOptionRo

MAX_FIELDS_LENGTH = 500
DEFAULT_VIEWS = [{"name": "Grid view", "type": "grid", "columnMeta": {}}]
_QUEUE = TaskQueue("import-table-csv-chunk")
_JOB_TTL_SECONDS = 60 * 60


def _random_string(n: int) -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return "".join(secrets.choice(alphabet) for _ in range(n))


class ImportService:
    async def analyze(self, ro: AnalyzeRo) -> dict[str, Any]:
        return await importer.gen_columns(ro.fileType, ro.attachmentUrl)

    def _fields_ro(self, columns: list[Any], tz: str) -> list[dict[str, Any]]:
        fields_ro: list[dict[str, Any]] = []
        for index, col in enumerate(columns):
            field: dict[str, Any] = {"name": col.name, "type": col.type}
            if index == 0:
                field["isPrimary"] = True
            if col.type == "date":
                field["options"] = {
                    "formatting": {
                        "timeZone": tz,
                        "date": "YYYY-MM-DD",
                        "time": "None",
                    }
                }
            fields_ro.append(field)
        return fields_ro

    async def _create_single_table(
        self, base_id: str, name: str, fields_ro: list[dict[str, Any]]
    ) -> dict[str, Any]:
        length = len(fields_ro)
        if length > MAX_FIELDS_LENGTH:
            raise ApiError(
                f"The number of fields in the table cannot exceed {MAX_FIELDS_LENGTH},"
                f" current is {length}",
                HttpErrorCode.VALIDATION_ERROR,
                {
                    "localization": {
                        "i18nKey": "httpErrors.import.exceedMaxFieldsLength",
                        "context": {"length": length, "maxFieldsLength": MAX_FIELDS_LENGTH},
                    }
                },
            )
        body = SimpleNamespace(
            name=name,
            fields=fields_ro,
            views=[dict(v) for v in DEFAULT_VIEWS],
            records=[],
            fieldKeyType=None,
            dbTableName=None,
        )
        return await TableService().create_table(base_id, body)

    async def create_table_from_import(
        self, base_id: str, ro: ImportOptionRo
    ) -> list[dict[str, Any]]:
        user_id = cls.get("user.id")
        from ..base_node.service import BaseNodeService

        node_service = BaseNodeService()
        folder_node_id = (
            await node_service.resolve_folder_node_id(base_id, ro.folderId)
            if ro.folderId
            else None
        )
        results: list[dict[str, Any]] = []
        for sheet_key, sheet in ro.worksheets.items():
            columns = sheet.columns
            column_info = columns if columns else []
            fields_ro = self._fields_ro(column_info, ro.tz)
            created = await self._create_single_table(base_id, sheet.name, fields_ro)
            results.append(created)

            if folder_node_id:
                # best-effort: on failure the table stays at root via node reconciliation
                try:
                    await node_service.attach_resource_to_parent(
                        base_id, folder_node_id, "table", created["id"]
                    )
                except ApiError:
                    pass

            if sheet.importData and columns:
                job_id = f"import-table-csv-chunk:{created['id']}:{_random_string(6)}"
                data = {
                    "jobId": job_id,
                    "mode": "create",
                    "baseId": base_id,
                    "tableId": created["id"],
                    "tableName": created["name"],
                    "userId": user_id,
                    "attachmentUrl": ro.attachmentUrl,
                    "fileType": ro.fileType,
                    "skipFirstNLines": 1 if sheet.useFirstRowAsHeader else 0,
                    "sheetKey": sheet_key,
                    "columnInfo": [
                        {"type": c.type, "name": c.name, "sourceColumnIndex": c.sourceColumnIndex}
                        for c in columns
                    ],
                    "fields": [
                        {"id": f["id"], "name": f["name"], "type": f["type"]}
                        for f in created["fields"]
                    ],
                }
                await _QUEUE.add("import_csv_chunk", data, job_id=job_id)
                await get_cache().set_detail(
                    latest_job_key(created["id"]), job_id, _JOB_TTL_SECONDS
                )
        return results

    async def inplace_import_table(
        self, base_id: str, table_id: str, ro: InplaceImportOptionRo
    ) -> None:
        user_id = cls.get("user.id")
        table = await FieldService()._load_table(table_id)
        fields = await FieldService().list_fields(table_id)

        insert = ro.insertConfig
        job_id = f"import-table-csv-chunk:{table_id}:{_random_string(6)}"
        data = {
            "jobId": job_id,
            "mode": "inplace",
            "baseId": base_id,
            "tableId": table_id,
            "tableName": table["name"],
            "userId": user_id,
            "attachmentUrl": ro.attachmentUrl,
            "fileType": ro.fileType,
            "skipFirstNLines": 1 if insert.excludeFirstRow else 0,
            "sheetKey": insert.sourceWorkSheetKey,
            "sourceColumnMap": insert.sourceColumnMap,
            "fields": [
                {"id": f["id"], "name": f["name"], "type": f["type"]} for f in fields
            ],
        }
        await _QUEUE.add("import_csv_chunk", data, job_id=job_id)
        await get_cache().set_detail(latest_job_key(table_id), job_id, _JOB_TTL_SECONDS)

    async def get_import_status(self, table_id: str) -> dict[str, Any]:
        latest = await get_cache().get(latest_job_key(table_id))
        if not latest:
            return {"tableId": table_id, "status": "not_found"}
        job = await _QUEUE.get_job(latest)
        if job is None:
            return {"tableId": table_id, "status": "not_found"}
        state = await job.get_state()
        status = _map_state(state)
        result: dict[str, Any] = {"tableId": table_id, "status": status}
        if status in ("completed", "failed"):
            manifest = await get_cache().get(result_manifest_key(latest))
            if isinstance(manifest, dict):
                if manifest.get("successCount") is not None:
                    result["successCount"] = manifest["successCount"]
                if manifest.get("failedCount") is not None:
                    result["failedCount"] = manifest["failedCount"]
                if manifest.get("errorReportUrl") is not None:
                    result["errorReportUrl"] = manifest["errorReportUrl"]
            elif isinstance(job.returnvalue, dict):
                rv = job.returnvalue
                if rv.get("success") is not None:
                    result["successCount"] = rv["success"]
                if rv.get("failed") is not None:
                    result["failedCount"] = rv["failed"]
        if status in ("running", "pending") and isinstance(job.progress, dict):
            if job.progress.get("successCount") is not None:
                result["successCount"] = job.progress["successCount"]
            if job.progress.get("failedCount") is not None:
                result["failedCount"] = job.progress["failedCount"]
        if status == "failed":
            result["message"] = job.failed_reason or "Import failed"
        return result


def _map_state(state: str) -> str:
    if state in ("waiting", "delayed"):
        return "pending"
    if state == "active":
        return "running"
    if state == "completed":
        return "completed"
    if state == "failed":
        return "failed"
    return "not_found"
