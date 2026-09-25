"""ShareDB snapshot adapter: collection → wedoc read service dispatch.

Collections are ``{docType}_{collectionId}`` where docType is an IdPrefix.
For record/field/view the collectionId is the tableId; for table it is the
baseId. Snapshots come from the same read services the REST layer uses, so the
socket and HTTP fallback paths return byte-identical data.
"""

from __future__ import annotations

from typing import Any

from ..core.ids import IdPrefix

DocType = str

_RECORD = IdPrefix.RECORD
_FIELD = IdPrefix.FIELD
_VIEW = IdPrefix.VIEW
_TABLE = IdPrefix.TABLE


def split_collection(collection: str) -> tuple[str, str]:
    doc_type, _, collection_id = collection.partition("_")
    return doc_type, collection_id


async def snapshot_bulk(
    collection: str,
    ids: list[str],
    projection: dict[str, bool] | None = None,
) -> list[dict[str, Any]]:
    doc_type, collection_id = split_collection(collection)
    if doc_type == _RECORD:
        from ..modules.record.service import RecordService

        return await RecordService().socket_snapshot_bulk(collection_id, ids, projection)
    if doc_type == _FIELD:
        from ..modules.field.service import FieldService

        return await FieldService().socket_snapshot_bulk(collection_id, ids)
    if doc_type == _VIEW:
        from ..modules.view.service import ViewService

        return await ViewService().socket_snapshot_bulk(collection_id, ids)
    if doc_type == _TABLE:
        from ..modules.table.service import TableService

        return await TableService().socket_snapshot_bulk(collection_id, ids)
    raise ValueError(f"unknown collection docType: {doc_type}")


async def doc_ids(collection: str, query: dict[str, Any] | None = None) -> dict[str, Any]:
    doc_type, collection_id = split_collection(collection)
    if doc_type == _RECORD:
        from ..modules.record.service import RecordService

        return await RecordService().socket_doc_ids(collection_id, query)
    if doc_type == _FIELD:
        from ..modules.field.service import FieldService

        return await FieldService().socket_doc_ids(collection_id, query)
    if doc_type == _VIEW:
        from ..modules.view.service import ViewService

        return await ViewService().socket_doc_ids(collection_id, query)
    if doc_type == _TABLE:
        from ..modules.table.service import TableService

        return await TableService().socket_doc_ids(collection_id, query)
    raise ValueError(f"unknown collection docType: {doc_type}")


async def snapshot(collection: str, doc_id: str) -> dict[str, Any]:
    """Return a single snapshot, or a v0/None placeholder when missing."""
    results = await snapshot_bulk(collection, [doc_id])
    for snap in results:
        if snap["id"] == doc_id:
            return snap
    return {"id": doc_id, "v": 0, "type": None, "data": None}
