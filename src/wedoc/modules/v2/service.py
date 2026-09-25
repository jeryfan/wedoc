"""v2 table endpoints — reuse the existing table/record services, serialize to
the v2-core DTO shape and wrap in the orpc {ok,data,events} envelope."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from ...core import cls
from ..record.schemas import RecordBulkPatchBody, RecordBulkPatchItem
from ..record.service import RecordService
from ..table.service import TableService

_FIELD_KEYS = (
    "id",
    "name",
    "dbFieldName",
    "isPrimary",
    "unique",
    "cellValueType",
    "dbFieldType",
    "type",
    "options",
)


class V2Error(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _field_dto(field: dict[str, Any]) -> dict[str, Any]:
    out = {k: field[k] for k in _FIELD_KEYS if k in field}
    for opt in ("description", "notNull", "isComputed", "hasError", "isMultipleCellValue"):
        if field.get(opt) is not None:
            out[opt] = field[opt]
    return out


def _view_dto(view: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"id": view["id"], "name": view["name"], "type": view["type"]}
    if view.get("description") is not None:
        out["description"] = view["description"]
    out["columnMeta"] = view.get("columnMeta") or {}
    return out


def _table_dto(vo: dict[str, Any], base_id: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": vo["id"],
        "baseId": base_id,
        "name": vo["name"],
    }
    if vo.get("description") is not None:
        out["description"] = vo["description"]
    if vo.get("icon") is not None:
        out["icon"] = vo["icon"]
    if vo.get("dbTableName") is not None:
        out["dbTableName"] = vo["dbTableName"]
    out["fields"] = [_field_dto(f) for f in vo.get("fields", [])]
    out["views"] = [_view_dto(v) for v in vo.get("views", [])]
    return out


class V2Service:
    async def create_table(self, payload: dict[str, Any]) -> dict[str, Any]:
        base_id = payload["baseId"]
        body = SimpleNamespace(
            name=payload["name"],
            dbTableName=payload.get("dbTableName"),
            fields=payload.get("fields") or [{"name": "Label", "type": "singleLineText"}],
            views=payload.get("views") or [{"name": "Grid", "type": "grid"}],
            records=payload.get("records") if payload.get("records") is not None else [],
            fieldKeyType=payload.get("fieldKeyType"),
        )
        try:
            vo = await TableService().create_table(base_id, body)
        except Exception as error:
            raise self._map_error(error) from error
        return {
            "table": _table_dto(vo, base_id),
            "events": [{"name": "TableCreated", "occurredAt": _now_iso()}],
        }

    async def get_table(self, payload: dict[str, Any]) -> dict[str, Any]:
        from ..field.service import FieldService
        from ..view.service import ViewService

        base_id = payload["baseId"]
        table_id = payload["tableId"]
        try:
            vo = await TableService().get_table(base_id, table_id)
            vo["fields"] = await FieldService().list_fields(table_id)
            vo["views"] = await ViewService().list_views(table_id)
        except Exception as error:
            raise self._map_error(error) from error
        dto = _table_dto(vo, base_id)
        # getById enriches the table DTO with the async-compute summary
        dto["computeMeta"] = {
            "computeMode": "server",
            "calculatingFieldCount": 0,
            "queuedFieldCount": 0,
            "status": "idle",
        }
        return {"table": dto}

    async def get_compute_activity(self, payload: dict[str, Any]) -> dict[str, Any]:
        base_id = payload["baseId"]
        table_id = payload["tableId"]
        try:
            await TableService().get_table(base_id, table_id)
        except Exception as error:
            raise self._map_error(error) from error
        # No async computed fields in wedoc yet: report an idle, healthy server.
        return {
            "tableId": table_id,
            "baseId": base_id,
            "table": None,
            "fields": [],
            "diagnostics": {
                "computeMode": "server",
                "executionState": "running",
                "activeFieldCount": 0,
                "queuedFieldCount": 0,
                "calculatingFieldCount": 0,
                "failedFieldCount": 0,
                "highComplexityFieldCount": 0,
                "anomalies": [],
                "pause": {
                    "effective": False,
                    "blockers": [],
                    "queuedTaskCount": 0,
                    "oldestQueuedAt": None,
                },
            },
        }

    async def delete_records(self, payload: dict[str, Any]) -> dict[str, Any]:
        table_id = payload["tableId"]
        record_ids = payload["recordIds"]
        try:
            result = await RecordService().delete_records(table_id, record_ids)
        except Exception as error:
            raise self._map_error(error) from error
        deleted = [r["id"] for r in result.get("records", [])]
        events = (
            [{"name": "RecordsDeleted", "occurredAt": _now_iso()}] if deleted else []
        )
        return {"deletedRecordIds": deleted, "events": events}

    async def update_records(self, payload: dict[str, Any]) -> dict[str, Any]:
        table_id = payload["tableId"]
        records_in = payload["records"]
        field_key_type = payload.get("fieldKeyType", "name")
        body = RecordBulkPatchBody(
            records=[
                RecordBulkPatchItem(id=r["id"], fields=r.get("fields") or {})
                for r in records_in
            ],
            fieldKeyType=field_key_type,
        )
        record_ids = [r["id"] for r in records_in]
        try:
            results = await RecordService().update_records(table_id, record_ids, body)
        except Exception as error:
            raise self._map_error(error) from error
        return {
            "updatedCount": len(results),
            "records": [{"id": r["id"], "fields": r["fields"]} for r in results],
        }

    def _map_error(self, error: Exception) -> V2Error:
        from ...core.errors import ApiError

        if isinstance(error, ApiError):
            status = error.status if error.status in (400, 401, 403, 404) else 500
            return V2Error(status, error.message)
        _ = cls  # keep import used
        return V2Error(500, "Internal server error")
