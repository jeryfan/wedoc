"""Undo/redo replay for record operations (v1 engine).

Operations are captured on the per-window stack by the record service; here we
pop the newest entry, replay its inverse (undo) or forward (redo) effect
against the record service, and move the transformed entry onto the opposite
stack. Replay sets ``undoRedoReplaying`` in cls so the record service does not
re-capture the ops it emits during a replay.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import structlog

from ...core import cls
from ..record.schemas import RecordBulkPatchBody
from ..record.service import RecordService
from .stack import Operation, UndoRedoStackService

logger = structlog.get_logger(__name__)

ENGINE = "v1"
_MISSING_WINDOW = "Missing windowId for undo/redo operation"


def engine_for(result: dict[str, Any]) -> str:
    # Only the missing-windowId validation failure routes through the v2 engine
    # upstream (it is the sole result carrying errorCode); every real v1 replay
    # — empty, fulfilled, or a replay-time failure — reports the v1 engine.
    return "v2" if result.get("errorCode") else ENGINE


class UndoRedoService:
    def __init__(self) -> None:
        self._stack = UndoRedoStackService()

    async def undo(self, table_id: str, window_id: str | None) -> dict[str, Any]:
        if not window_id:
            return {
                "status": "failed",
                "errorMessage": _MISSING_WINDOW,
                "errorCode": "validation.invalid",
            }
        operation, push = await self._stack.pop_undo(table_id, window_id)
        if not operation:
            return {"status": "empty"}
        try:
            new_operation = await self._replay(operation, mode="undo")
            await push(new_operation)
        except Exception as exc:
            logger.warning("undo failed", error=str(exc))
            return {"status": "failed", "errorMessage": str(exc)}
        return {"status": "fulfilled"}

    async def redo(self, table_id: str, window_id: str | None) -> dict[str, Any]:
        if not window_id:
            return {
                "status": "failed",
                "errorMessage": _MISSING_WINDOW,
                "errorCode": "validation.invalid",
            }
        operation, push = await self._stack.pop_redo(table_id, window_id)
        if not operation:
            return {"status": "empty"}
        try:
            new_operation = await self._replay(operation, mode="redo")
            await push(new_operation)
        except Exception as exc:
            logger.warning("redo failed", error=str(exc))
            return {"status": "failed", "errorMessage": str(exc)}
        return {"status": "fulfilled"}

    async def undo_stream(
        self, table_id: str, window_id: str | None
    ) -> AsyncIterator[dict[str, Any]]:
        result = await self.undo(table_id, window_id)
        yield self._terminal_event("undo", result)

    async def redo_stream(
        self, table_id: str, window_id: str | None
    ) -> AsyncIterator[dict[str, Any]]:
        result = await self.redo(table_id, window_id)
        yield self._terminal_event("redo", result)

    @staticmethod
    def _terminal_event(mode: str, result: dict[str, Any]) -> dict[str, Any]:
        if result["status"] == "failed":
            event = {
                "id": "error",
                "mode": mode,
                "engine": engine_for(result),
                "message": result.get("errorMessage", "Undo/redo failed"),
            }
            if result.get("errorCode"):
                event["code"] = result["errorCode"]
            return event
        return {"id": "done", "mode": mode, "engine": ENGINE, "status": result["status"]}

    # ---- replay ------------------------------------------------------------

    async def _replay(self, operation: Operation, mode: str) -> Operation:
        previous = cls.get("undoRedoReplaying")
        cls.set("undoRedoReplaying", True)
        try:
            name = operation["name"]
            handler = {
                "updateRecords": self._replay_update,
                "createRecords": self._replay_create,
                "deleteRecords": self._replay_delete,
                "createFields": self._replay_create_fields,
                "deleteFields": self._replay_delete_fields,
                "createView": self._replay_create_view,
                "deleteView": self._replay_delete_view,
                "updateView": self._replay_update_view,
                "updateRecordsOrder": self._replay_update_records_order,
                "pasteSelection": self._replay_paste_selection,
            }.get(name)
            if handler is None:
                raise ValueError(f"unsupported operation: {name}")
            await handler(operation, mode)
        finally:
            cls.set("undoRedoReplaying", previous)
        return operation

    async def _replay_update(self, operation: Operation, mode: str) -> None:
        table_id = operation["params"]["tableId"]
        contexts = operation["result"]["cellContexts"]
        value_key = "oldValue" if mode == "undo" else "newValue"
        by_record: dict[str, dict[str, Any]] = {}
        for ctx in contexts:
            by_record.setdefault(ctx["recordId"], {})[ctx["fieldId"]] = ctx[value_key]
        record_ids = list(by_record.keys())
        body = RecordBulkPatchBody.zod_validate(
            {
                "fieldKeyType": "id",
                "records": [
                    {"id": rid, "fields": fields} for rid, fields in by_record.items()
                ],
            }
        )
        await RecordService().update_records(table_id, record_ids, body)

    async def _replay_create(self, operation: Operation, mode: str) -> None:
        table_id = operation["params"]["tableId"]
        records = operation["result"]["records"]
        service = RecordService()
        if mode == "undo":
            await service.delete_records(table_id, [r["id"] for r in records])
        else:
            await service.restore_records(table_id, records)

    async def _replay_delete(self, operation: Operation, mode: str) -> None:
        table_id = operation["params"]["tableId"]
        records = operation["result"]["records"]
        service = RecordService()
        if mode == "undo":
            await service.restore_records(table_id, records)
        else:
            await service.delete_records(table_id, [r["id"] for r in records])

    # ---- fields ------------------------------------------------------------

    async def _replay_create_fields(self, operation: Operation, mode: str) -> None:
        from ..field.service import FieldService

        table_id = operation["params"]["tableId"]
        fields = operation["result"]["fields"]
        service = FieldService()
        if mode == "undo":
            for field in fields:
                await service.delete_field(table_id, field["id"])
        else:
            for field in fields:
                await service.restore_field(table_id, field["id"])

    async def _replay_delete_fields(self, operation: Operation, mode: str) -> None:
        from ..field.service import FieldService

        table_id = operation["params"]["tableId"]
        fields = operation["result"]["fields"]
        service = FieldService()
        if mode == "undo":
            for field in fields:
                await service.restore_field(table_id, field["id"])
        else:
            for field in fields:
                await service.delete_field(table_id, field["id"])

    # ---- views -------------------------------------------------------------

    async def _replay_create_view(self, operation: Operation, mode: str) -> None:
        from ..view.service import ViewService

        table_id = operation["params"]["tableId"]
        view_id = operation["result"]["view"]["id"]
        service = ViewService()
        if mode == "undo":
            await service.delete_view(table_id, view_id)
        else:
            await service.restore_view(table_id, view_id)

    async def _replay_delete_view(self, operation: Operation, mode: str) -> None:
        from ..view.service import ViewService

        table_id = operation["params"]["tableId"]
        view_id = operation["params"]["viewId"]
        service = ViewService()
        if mode == "undo":
            await service.restore_view(table_id, view_id)
        else:
            await service.delete_view(table_id, view_id)

    async def _replay_update_view(self, operation: Operation, mode: str) -> None:
        from ..view.service import ViewService

        table_id = operation["params"]["tableId"]
        view_id = operation["params"]["viewId"]
        by_key = operation["result"].get("byKey")
        if not by_key:
            return
        value = by_key["oldValue"] if mode == "undo" else by_key["newValue"]
        await ViewService().update_json_prop(
            table_id, view_id, by_key["key"], value, validate=False
        )

    async def _replay_update_records_order(self, operation: Operation, mode: str) -> None:
        from ..view.service import ViewService

        table_id = operation["params"]["tableId"]
        view_id = operation["params"]["viewId"]
        orders_map = operation["result"].get("ordersMap") or {}
        key = "oldOrder" if mode == "undo" else "newOrder"
        orders: dict[str, float] = {}
        for record_id, spec in orders_map.items():
            value = (spec.get(key) or {}).get(view_id)
            if value is not None:
                orders[record_id] = value
        if orders:
            await ViewService().restore_record_orders(table_id, view_id, orders)

    # ---- paste (composite) -------------------------------------------------

    async def _replay_paste_selection(self, operation: Operation, mode: str) -> None:
        from ..field.service import FieldService

        table_id = operation["params"]["tableId"]
        result = operation["result"]
        update_records = result.get("updateRecords")
        new_fields = result.get("newFields") or []
        new_records = result.get("newRecords") or []
        record_service = RecordService()
        field_service = FieldService()

        if mode == "undo":
            if update_records:
                await self._apply_cell_contexts(
                    record_service, table_id, update_records["cellContexts"], "oldValue"
                )
            if new_records:
                await record_service.delete_records(table_id, [r["id"] for r in new_records])
            for field in new_fields:
                await field_service.delete_field(table_id, field["id"])
        else:
            for field in new_fields:
                await field_service.restore_field(table_id, field["id"])
            if update_records:
                await self._apply_cell_contexts(
                    record_service, table_id, update_records["cellContexts"], "newValue"
                )
            if new_records:
                await record_service.restore_records(table_id, new_records)

    @staticmethod
    async def _apply_cell_contexts(
        record_service: RecordService,
        table_id: str,
        contexts: list[dict[str, Any]],
        value_key: str,
    ) -> None:
        by_record: dict[str, dict[str, Any]] = {}
        for ctx in contexts:
            by_record.setdefault(ctx["recordId"], {})[ctx["fieldId"]] = ctx[value_key]
        if not by_record:
            return
        body = RecordBulkPatchBody.zod_validate(
            {
                "fieldKeyType": "id",
                "records": [
                    {"id": rid, "fields": fields} for rid, fields in by_record.items()
                ],
            }
        )
        await record_service.update_records(table_id, list(by_record.keys()), body)
