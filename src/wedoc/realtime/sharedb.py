"""Server-side ShareDB agent (protocol 1.2), matching sharedb 5.2.2.

Actions carried on the wire (``message-actions.js``): ``init``/``hs`` handshake,
``f`` fetch, ``s``/``u`` (un)subscribe, ``bs``/``bu`` bulk (un)subscribe,
``op`` submit, ``qf``/``qs``/``qu`` query, ``p``/``ps``/``pu``/``pr`` presence,
``pp`` ping-pong. Writes flow through the REST API (the upstream ShareDB DB
adapter's ``commit`` is intentionally unimplemented); the socket streams
snapshots and rebroadcasts REST-driven ops via Redis pub/sub.

A ``qs`` subscription mirrors the upstream QueryEmitter: it subscribes to the
collection channel, forwards ops on documents already in the result set as
``op`` messages, and re-polls the query to emit ``q`` insert/remove/move diffs
when membership or order changes.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Callable
from typing import Any

import structlog

from ..core import cls
from . import adapter
from .arraydiff import array_diff
from .pubsub import RealtimePubSub, Stream

logger = structlog.get_logger(__name__)

PROTOCOL_MAJOR = 1
PROTOCOL_MINOR = 2
DEFAULT_TYPE_URI = "http://sharejs.org/types/JSONv0"
DEFAULT_TYPE_NAME = "json0"

# message-actions.js ACTIONS
A_INIT = "init"
A_HANDSHAKE = "hs"
A_QUERY_FETCH = "qf"
A_QUERY_SUBSCRIBE = "qs"
A_QUERY_UNSUBSCRIBE = "qu"
A_QUERY_UPDATE = "q"
A_BULK_FETCH = "bf"
A_BULK_SUBSCRIBE = "bs"
A_BULK_UNSUBSCRIBE = "bu"
A_FETCH = "f"
A_SUBSCRIBE = "s"
A_UNSUBSCRIBE = "u"
A_OP = "op"
A_SNAPSHOT_FETCH = "nf"
A_PING_PONG = "pp"
A_PRESENCE = "p"
A_PRESENCE_SUBSCRIBE = "ps"
A_PRESENCE_UNSUBSCRIBE = "pu"
A_PRESENCE_REQUEST = "pr"


def _client_id() -> str:
    return secrets.token_hex(16)


class _QuerySub:
    """State for one ``qs`` subscription: the collection op stream plus the
    current ordered id list the client mirrors."""

    __slots__ = (
        "collection",
        "destroyed",
        "extra",
        "ids",
        "pending",
        "polling",
        "query",
        "query_id",
        "ready",
        "stream",
    )

    def __init__(self, query_id: Any, collection: str, query: dict[str, Any]) -> None:
        self.query_id = query_id
        self.collection = collection
        self.query = query
        self.ids: list[str] = []
        self.extra: Any = None
        self.stream: Stream | None = None
        self.ready = False
        self.polling = False
        self.pending = False
        self.destroyed = False


class Agent:
    """One ShareDB connection. ``send`` enqueues a message dict to the socket."""

    def __init__(
        self,
        send: Callable[[dict[str, Any]], None],
        pubsub: RealtimePubSub,
        cls_context: dict[str, Any],
    ) -> None:
        self._send = send
        self._pubsub = pubsub
        self._cls_context = cls_context
        self.client_id = _client_id()
        self.src: str | None = None
        self.closed = False
        self.subscribed_docs: dict[str, dict[str, Stream]] = {}
        self.subscribed_presences: dict[str, Stream] = {}
        self.subscribed_queries: dict[Any, _QuerySub] = {}
        self._poll_tasks: set[asyncio.Task[None]] = set()
        # Legacy init packet primes clients with the agent id.
        self.send(self._init_message(A_INIT))

    def _src(self) -> str:
        return self.src or self.client_id

    def send(self, message: dict[str, Any]) -> None:
        if self.closed:
            return
        self._send(message)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        for docs in self.subscribed_docs.values():
            for stream in docs.values():
                stream.destroy()
        self.subscribed_docs.clear()
        for stream in self.subscribed_presences.values():
            stream.destroy()
        self.subscribed_presences.clear()
        for sub in self.subscribed_queries.values():
            sub.destroyed = True
            if sub.stream is not None:
                sub.stream.destroy()
        self.subscribed_queries.clear()
        for task in self._poll_tasks:
            task.cancel()
        self._poll_tasks.clear()

    def _init_message(self, action: str) -> dict[str, Any]:
        return {
            "a": action,
            "protocol": PROTOCOL_MAJOR,
            "protocolMinor": PROTOCOL_MINOR,
            "id": self._src(),
            "type": DEFAULT_TYPE_URI,
        }

    async def handle_message(self, request: dict[str, Any]) -> None:
        action = request.get("a")
        try:
            if action == A_HANDSHAKE:
                return self._handle_handshake(request)
            if action == A_FETCH:
                return await self._fetch(request)
            if action == A_SUBSCRIBE:
                return await self._subscribe(request)
            if action == A_UNSUBSCRIBE:
                return self._unsubscribe(request)
            if action == A_BULK_FETCH:
                return await self._bulk_fetch(request)
            if action == A_BULK_SUBSCRIBE:
                return await self._bulk_subscribe(request)
            if action == A_BULK_UNSUBSCRIBE:
                return self._bulk_unsubscribe(request)
            if action == A_QUERY_FETCH:
                return await self._query_fetch(request)
            if action == A_QUERY_SUBSCRIBE:
                return await self._query_subscribe(request)
            if action == A_QUERY_UNSUBSCRIBE:
                return self._query_unsubscribe(request)
            if action == A_OP:
                return self._submit(request)
            if action == A_SNAPSHOT_FETCH:
                return await self._fetch_snapshot(request)
            if action == A_PING_PONG:
                return self.send({"a": A_PING_PONG})
            if action == A_PRESENCE:
                return await self._broadcast_presence(request)
            if action == A_PRESENCE_SUBSCRIBE:
                return await self._subscribe_presence(request)
            if action == A_PRESENCE_UNSUBSCRIBE:
                return self._unsubscribe_presence(request)
            if action == A_PRESENCE_REQUEST:
                return await self._request_presence(request.get("ch"))
            self._reply_error(request, 4000, "Invalid or unknown message")
        except Exception as exc:
            logger.warning("sharedb message error", action=action, error=str(exc))
            self._reply_error(request, 5000, str(exc))

    # ---- replies -----------------------------------------------------------

    def _reply(self, request: dict[str, Any], message: dict[str, Any] | None = None) -> None:
        message = dict(message or {})
        message["a"] = request["a"]
        if request.get("id") is not None:
            message["id"] = request["id"]
        else:
            if request.get("c") is not None:
                message["c"] = request["c"]
            if request.get("d") is not None:
                message["d"] = request["d"]
        self.send(message)

    def _reply_error(self, request: dict[str, Any], code: int, msg: str) -> None:
        reply = dict(request)
        reply["error"] = {"code": code, "message": msg}
        self.send(reply)

    # ---- handshake ---------------------------------------------------------

    def _handle_handshake(self, request: dict[str, Any]) -> None:
        if request.get("id"):
            self.src = request["id"]
        self.send(self._init_message(A_HANDSHAKE))

    # ---- snapshots ---------------------------------------------------------

    async def _snapshot(self, collection: str, doc_id: str) -> dict[str, Any]:
        with _cls_scope(self._cls_context):
            return await adapter.snapshot(collection, doc_id)

    async def _snapshot_bulk(
        self, collection: str, ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        with _cls_scope(self._cls_context):
            found = await adapter.snapshot_bulk(collection, ids)
        by_id = {s["id"]: s for s in found}
        return {
            doc_id: by_id.get(doc_id, {"id": doc_id, "v": 0, "type": None, "data": None})
            for doc_id in ids
        }

    @staticmethod
    def _snapshot_data(snap: dict[str, Any]) -> dict[str, Any]:
        data = {"v": snap["v"], "data": snap["data"]}
        if snap.get("type") != DEFAULT_TYPE_NAME:
            data["type"] = snap.get("type")
        return data

    async def _fetch(self, request: dict[str, Any]) -> None:
        collection, doc_id = request["c"], request["d"]
        if request.get("v") is not None:
            # version given → client wants ops; ops never gap (REST is source)
            return self._reply(request)
        snap = await self._snapshot(collection, doc_id)
        self._reply(request, {"data": self._snapshot_data(snap)})

    async def _fetch_snapshot(self, request: dict[str, Any]) -> None:
        collection, doc_id = request["c"], request["d"]
        snap = await self._snapshot(collection, doc_id)
        self._reply(request, {"data": self._snapshot_data(snap)})

    async def _bulk_fetch(self, request: dict[str, Any]) -> None:
        collection = request["c"]
        ids = list(request.get("b") or [])
        snaps = await self._snapshot_bulk(collection, ids)
        data = {doc_id: self._snapshot_data(snap) for doc_id, snap in snaps.items()}
        self._reply(request, {"data": data})

    # ---- subscribe ---------------------------------------------------------

    async def _subscribe(self, request: dict[str, Any]) -> None:
        collection, doc_id = request["c"], request["d"]
        await self._subscribe_stream(collection, doc_id)
        if request.get("v") is None:
            snap = await self._snapshot(collection, doc_id)
            self._reply(request, {"data": self._snapshot_data(snap)})
        else:
            self._reply(request)

    async def _subscribe_stream(self, collection: str, doc_id: str) -> None:
        channel = f"{collection}.{doc_id}"
        stream = await self._pubsub.subscribe(channel)
        docs = self.subscribed_docs.setdefault(collection, {})
        previous = docs.get(doc_id)
        if previous:
            previous.destroy()
        docs[doc_id] = stream
        stream.on_data = lambda op: self._on_op(collection, doc_id, op)

    async def _bulk_subscribe(self, request: dict[str, Any]) -> None:
        collection = request["c"]
        versions = request.get("b") or {}
        ids = list(versions.keys()) if isinstance(versions, dict) else list(versions)
        for doc_id in ids:
            await self._subscribe_stream(collection, doc_id)
        snaps = await self._snapshot_bulk(collection, ids)
        data = {doc_id: self._snapshot_data(snap) for doc_id, snap in snaps.items()}
        self._reply(request, {"data": data})

    def _unsubscribe(self, request: dict[str, Any]) -> None:
        collection, doc_id = request["c"], request["d"]
        docs = self.subscribed_docs.get(collection)
        if docs and doc_id in docs:
            docs.pop(doc_id).destroy()
        self._reply(request)

    def _bulk_unsubscribe(self, request: dict[str, Any]) -> None:
        collection = request["c"]
        docs = self.subscribed_docs.get(collection) or {}
        for doc_id in list(request.get("b") or []):
            stream = docs.pop(doc_id, None)
            if stream:
                stream.destroy()
        self._reply(request)

    def _on_op(self, collection: str, doc_id: str, op: dict[str, Any]) -> None:
        # Skip ops this client authored on the same collection (echo).
        if op.get("src") == self._src() and collection == (op.get("i") or op.get("c")):
            return
        message: dict[str, Any] = {
            "a": A_OP,
            "c": collection,
            "d": doc_id,
            "v": op.get("v"),
            "src": op.get("src"),
            "seq": op.get("seq"),
        }
        if "op" in op:
            message["op"] = op["op"]
        if op.get("create"):
            message["create"] = op["create"]
        if op.get("del"):
            message["del"] = True
        self.send(message)

    # ---- op submit (writes go through REST; mirror upstream gate) ----------

    def _submit(self, request: dict[str, Any]) -> None:
        collection = request.get("c", "")
        doc_type, _ = adapter.split_collection(collection)
        ack = {
            "src": request.get("src") or self._src(),
            "seq": request.get("seq"),
            "v": request.get("v"),
        }
        if doc_type != "rec" or "op" not in request:
            return self._reply_error(request, 4025, "only record op can be committed")
        # ShareDB's DB.commit is unimplemented upstream: record writes flow
        # through the REST API, which then rebroadcasts the op via pub/sub.
        reply = dict(request)
        reply.update(ack)
        reply["error"] = {"code": 5019, "message": "Method not implemented."}
        self.send(reply)

    # ---- queries (doc-id listing for grids) --------------------------------

    async def _query_fetch(self, request: dict[str, Any]) -> None:
        collection = request["c"]
        query = request.get("q") or {}
        with _cls_scope(self._cls_context):
            result = await adapter.doc_ids(collection, query)
        ids = result.get("ids", [])
        snaps = await self._snapshot_bulk(collection, ids)
        data = []
        for doc_id in ids:
            item = self._snapshot_data(snaps[doc_id])
            item["d"] = doc_id
            data.append(item)
        extra = result.get("extra")
        self._reply(request, {"data": data, "extra": extra})

    async def _query_subscribe(self, request: dict[str, Any]) -> None:
        collection = request["c"]
        query = request.get("q") or {}
        query_id = request.get("id")
        sub = _QuerySub(query_id, collection, query)
        # Subscribe before the initial poll so no op is missed once results are
        # established; ops delivered before ``ready`` are ignored — the initial
        # poll already reflects committed state.
        stream = await self._pubsub.subscribe(collection)
        sub.stream = stream
        stream.on_data = lambda op, s=sub: self._on_query_op(s, op)
        self.subscribed_queries[query_id] = sub
        with _cls_scope(self._cls_context):
            result = await adapter.doc_ids(collection, query)
        ids = list(result.get("ids", []))
        snaps = await self._snapshot_bulk(collection, ids)
        data = []
        for doc_id in ids:
            item = self._snapshot_data(snaps[doc_id])
            item["d"] = doc_id
            data.append(item)
        sub.ids = ids
        sub.extra = result.get("extra")
        sub.ready = True
        self._reply(request, {"data": data, "extra": sub.extra})

    def _query_unsubscribe(self, request: dict[str, Any]) -> None:
        query_id = request.get("id")
        sub = self.subscribed_queries.pop(query_id, None)
        if sub is not None:
            sub.destroyed = True
            if sub.stream is not None:
                sub.stream.destroy()
        self.send({"a": A_QUERY_UNSUBSCRIBE, "id": query_id})

    def _on_query_op(self, sub: _QuerySub, op: dict[str, Any]) -> None:
        # Forward ops on docs already in the result set (matching upstream
        # QueryEmitter.onOp) so live edits reach the doc, then re-poll to emit
        # membership/order diffs.
        if sub.destroyed or not sub.ready:
            return
        doc_id = op.get("d")
        if doc_id in sub.ids:
            self._on_op(sub.collection, doc_id, op)
        self._schedule_poll(sub)

    def _schedule_poll(self, sub: _QuerySub) -> None:
        if sub.polling:
            sub.pending = True
            return
        sub.polling = True
        task = asyncio.create_task(self._run_poll(sub))
        self._poll_tasks.add(task)
        task.add_done_callback(self._poll_tasks.discard)

    async def _run_poll(self, sub: _QuerySub) -> None:
        try:
            await self._poll_once(sub)
            while sub.pending and not sub.destroyed and not self.closed:
                sub.pending = False
                await self._poll_once(sub)
        finally:
            sub.polling = False

    async def _poll_once(self, sub: _QuerySub) -> None:
        if sub.destroyed or self.closed:
            return
        with _cls_scope(self._cls_context):
            result = await adapter.doc_ids(sub.collection, sub.query)
        new_ids = list(result.get("ids", []))
        extra = result.get("extra")
        if extra != sub.extra:
            sub.extra = extra
            self.send({"a": A_QUERY_UPDATE, "id": sub.query_id, "extra": extra})
        diff = array_diff(sub.ids, new_ids)
        sub.ids = new_ids
        if not diff:
            return
        inserted: list[str] = []
        for item in diff:
            if item["type"] == "insert":
                inserted.extend(item["values"])
        snaps = await self._snapshot_bulk(sub.collection, inserted) if inserted else {}
        for item in diff:
            if item["type"] != "insert":
                continue
            values = []
            for doc_id in item["values"]:
                snapshot = self._snapshot_data(snaps[doc_id])
                snapshot["d"] = doc_id
                values.append(snapshot)
            item["values"] = values
        self.send({"a": A_QUERY_UPDATE, "id": sub.query_id, "diff": diff})

    # ---- presence ----------------------------------------------------------

    def _presence_channel(self, channel: str) -> str:
        return "$presence." + channel

    async def _broadcast_presence(self, request: dict[str, Any]) -> None:
        # Doc presence carries the document type; json0 (ot-json0) does not
        # implement presence, so upstream rejects it before broadcasting.
        presence_type = request.get("t")
        if presence_type and presence_type == DEFAULT_TYPE_URI:
            reply = dict(request)
            reply["error"] = {
                "code": "ERR_TYPE_DOES_NOT_SUPPORT_PRESENCE",
                "message": f"Type does not support presence: {presence_type}",
            }
            self.send(reply)
            return
        presence = {
            "a": A_PRESENCE,
            "ch": request.get("ch"),
            "src": self._src(),
            "id": request.get("id"),
            "p": request.get("p"),
            "pv": request.get("pv"),
            "c": request.get("c"),
            "d": request.get("d"),
            "v": request.get("v"),
            "t": request.get("t"),
        }
        await self._pubsub.publish([self._presence_channel(request.get("ch"))], presence)
        self._reply(request)

    async def _subscribe_presence(self, request: dict[str, Any]) -> None:
        channel = request.get("ch")
        seq = request.get("seq")
        if channel not in self.subscribed_presences:
            stream = await self._pubsub.subscribe(self._presence_channel(channel))
            self.subscribed_presences[channel] = stream
            stream.on_data = self._on_presence
            await self._request_presence(channel)
        self.send({"a": A_PRESENCE_SUBSCRIBE, "ch": channel, "seq": seq})

    def _unsubscribe_presence(self, request: dict[str, Any]) -> None:
        channel = request.get("ch")
        stream = self.subscribed_presences.pop(channel, None)
        if stream:
            stream.destroy()
        self.send({"a": A_PRESENCE_UNSUBSCRIBE, "ch": channel, "seq": request.get("seq")})

    async def _request_presence(self, channel: str) -> None:
        await self._pubsub.publish(
            [self._presence_channel(channel)],
            {"ch": channel, "r": True, "src": self.client_id},
        )

    def _on_presence(self, presence: dict[str, Any]) -> None:
        if presence.get("src") == self._src():
            return
        if presence.get("r"):
            self.send({"a": A_PRESENCE_REQUEST, "ch": presence.get("ch")})
            return
        self.send(presence)


class _cls_scope:
    """Context manager entering a cls store for the duration of a service call."""

    def __init__(self, context: dict[str, Any]) -> None:
        self._context = context
        self._token: Any = None

    def __enter__(self) -> None:
        self._token = cls.enter(self._context)

    def __exit__(self, *exc: object) -> None:
        cls.exit(self._token)
