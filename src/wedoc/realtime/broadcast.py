"""Publish REST-driven mutations as ShareDB ops on the pub/sub bus.

Writes never commit through the socket (see ``sharedb.Agent._submit``); instead
the REST services call these helpers after a mutation so that every subscriber
of the doc — and the collection's query subscribers — receives the op, exactly
as the upstream backend rebroadcasts ``rawOpMap`` entries after a transaction
(``publishOpsMap`` fans each op to ``[collection, collection.docId]``).
"""

from __future__ import annotations

import itertools
import secrets
from typing import Any

from ..core.ids import IdPrefix
from .pubsub import get_pubsub


def _rand_src() -> str:
    return secrets.token_hex(11)[:21]


# action-trigger presence is server-originated (no client agent), so it needs a
# stable src with a strictly increasing presence version; a client dedups by
# (src, pv) and drops any frame whose pv is not greater than the last seen.
_ACTION_TRIGGER_SRC = _rand_src()
_action_trigger_pv = itertools.count(1)


def _action_trigger_channel(id_: str) -> str:
    return f"__action_trigger_{id_}"


async def broadcast_action_trigger(id_: str, data: list[dict[str, Any]]) -> None:
    """Publish a table/view action-trigger as ShareDB presence.

    ``id_`` is the tableId (record/field actions) or viewId (view actions); the
    frontend subscribes presence on ``__action_trigger_{id}`` and refreshes row
    count / aggregation / group stats when a matching ``actionKey`` arrives.
    """
    if not data:
        return
    channel = _action_trigger_channel(id_)
    presence = {
        "a": "p",
        "ch": channel,
        "src": _ACTION_TRIGGER_SRC,
        "id": id_,
        "p": data,
        "pv": next(_action_trigger_pv),
    }
    await get_pubsub().publish([f"$presence.{channel}"], presence)


async def broadcast_notification(
    user_id: str, notification_id: str, payload: dict[str, Any]
) -> None:
    """Push a notification to its recipient over ShareDB presence.

    Mirrors the reference ``sendNotifyBySocket``: the recipient's client
    subscribes presence on ``__notification_user_{userId}`` and appends the feed
    entry / bumps the unread badge from ``payload``.
    """
    channel = f"__notification_user_{user_id}"
    presence = {
        "a": "p",
        "ch": channel,
        "src": _ACTION_TRIGGER_SRC,
        "id": notification_id,
        "p": payload,
        "pv": next(_action_trigger_pv),
    }
    await get_pubsub().publish([f"$presence.{channel}"], presence)


def build_set_record_op(field_id: str, new_value: Any, old_value: Any) -> dict[str, Any]:
    """json0 op for a record cell change, matching SetRecordBuilder.build."""
    path = ["fields", field_id]
    new_value = new_value if new_value is not None else None
    old_value = old_value if old_value is not None else None
    if new_value is None or (isinstance(new_value, list) and len(new_value) == 0):
        return {"p": path, "od": old_value, "oi": None}
    if old_value is None:
        return {"p": path, "oi": new_value}
    return {"p": path, "od": old_value, "oi": new_value}


def set_property_op(key: str, new_value: Any, old_value: Any) -> dict[str, Any]:
    """json0 op for a top-level property change, matching the SetProperty
    builders shared by field/view/table docs."""
    op: dict[str, Any] = {"p": [key]}
    if new_value is not None:
        op["oi"] = new_value
    if old_value is not None:
        op["od"] = old_value
    return op


def build_set_property_ops(
    old: dict[str, Any], new: dict[str, Any], ignore: tuple[str, ...] = ()
) -> list[dict[str, Any]]:
    """Diff two value objects into per-key SetProperty ops (id and audit-only
    keys excluded)."""
    ops: list[dict[str, Any]] = []
    for key in list(new.keys()) + [k for k in old if k not in new]:
        if key == "id" or key in ignore:
            continue
        old_value = old.get(key)
        new_value = new.get(key)
        if old_value == new_value:
            continue
        ops.append(set_property_op(key, new_value, old_value))
    return ops


async def broadcast_record_edit(
    table_id: str, record_id: str, ops: list[dict[str, Any]], version: int
) -> None:
    if not ops:
        return
    collection = f"rec_{table_id}"
    raw = {
        "src": _rand_src(),
        "seq": 1,
        "v": max(version - 1, 0),
        "op": ops,
        "c": collection,
        "d": record_id,
    }
    await _publish(collection, record_id, raw)


async def broadcast_record_create(
    table_id: str, record_id: str, data: dict[str, Any]
) -> None:
    collection = f"rec_{table_id}"
    raw = {
        "src": _rand_src(),
        "seq": 1,
        "v": 0,
        "create": {"type": "json0", "data": data},
        "c": collection,
        "d": record_id,
    }
    await _publish(collection, record_id, raw)


async def broadcast_record_delete(
    table_id: str, record_id: str, version: int
) -> None:
    collection = f"rec_{table_id}"
    raw = {
        "src": _rand_src(),
        "seq": 1,
        "v": max(version, 0),
        "del": True,
        "c": collection,
        "d": record_id,
    }
    await _publish(collection, record_id, raw)


async def _publish(collection: str, doc_id: str, raw: dict[str, Any]) -> None:
    channels = [collection, f"{collection}.{doc_id}"]
    await get_pubsub().publish(channels, raw)


async def broadcast_doc_create(collection: str, doc_id: str, data: dict[str, Any]) -> None:
    raw = {
        "src": _rand_src(),
        "seq": 1,
        "v": 0,
        "create": {"type": "json0", "data": data},
        "c": collection,
        "d": doc_id,
    }
    await _publish(collection, doc_id, raw)


async def broadcast_doc_update(
    collection: str, doc_id: str, ops: list[dict[str, Any]], version: int
) -> None:
    if not ops:
        return
    raw = {
        "src": _rand_src(),
        "seq": 1,
        "v": max(version - 1, 0),
        "op": ops,
        "c": collection,
        "d": doc_id,
    }
    await _publish(collection, doc_id, raw)


async def broadcast_doc_delete(collection: str, doc_id: str, version: int) -> None:
    raw = {
        "src": _rand_src(),
        "seq": 1,
        "v": max(version, 0),
        "del": True,
        "c": collection,
        "d": doc_id,
    }
    await _publish(collection, doc_id, raw)


async def broadcast_field_create(table_id: str, data: dict[str, Any]) -> None:
    await broadcast_doc_create(f"{IdPrefix.FIELD}_{table_id}", data["id"], data)


async def broadcast_field_update(
    table_id: str, field_id: str, ops: list[dict[str, Any]], version: int
) -> None:
    await broadcast_doc_update(f"{IdPrefix.FIELD}_{table_id}", field_id, ops, version)


async def broadcast_field_delete(table_id: str, field_id: str, version: int) -> None:
    await broadcast_doc_delete(f"{IdPrefix.FIELD}_{table_id}", field_id, version)


async def broadcast_view_create(table_id: str, data: dict[str, Any]) -> None:
    await broadcast_doc_create(f"{IdPrefix.VIEW}_{table_id}", data["id"], data)


async def broadcast_view_update(
    table_id: str, view_id: str, ops: list[dict[str, Any]], version: int
) -> None:
    await broadcast_doc_update(f"{IdPrefix.VIEW}_{table_id}", view_id, ops, version)


async def broadcast_view_delete(table_id: str, view_id: str, version: int) -> None:
    await broadcast_doc_delete(f"{IdPrefix.VIEW}_{table_id}", view_id, version)


async def broadcast_table_create(base_id: str, data: dict[str, Any]) -> None:
    await broadcast_doc_create(f"{IdPrefix.TABLE}_{base_id}", data["id"], data)


async def broadcast_table_update(
    base_id: str, table_id: str, ops: list[dict[str, Any]], version: int
) -> None:
    await broadcast_doc_update(f"{IdPrefix.TABLE}_{base_id}", table_id, ops, version)


async def broadcast_table_delete(base_id: str, table_id: str, version: int) -> None:
    await broadcast_doc_delete(f"{IdPrefix.TABLE}_{base_id}", table_id, version)
