"""Unit tests for the realtime stack: json0 OT, SockJS framing, ShareDB routing."""

import asyncio

import pytest

from wedoc.realtime import json0
from wedoc.realtime.broadcast import build_set_record_op
from wedoc.realtime.sharedb import Agent
from wedoc.realtime.sockjs import SockJSConnection, _encode_array


class TestJson0Apply:
    def test_object_insert(self):
        doc = {"fields": {}}
        out = json0.apply(doc, [{"p": ["fields", "fldA"], "oi": "x"}])
        assert out == {"fields": {"fldA": "x"}}
        # original snapshot untouched
        assert doc == {"fields": {}}

    def test_object_replace(self):
        doc = {"fields": {"fldA": "x"}}
        out = json0.apply(doc, [{"p": ["fields", "fldA"], "od": "x", "oi": "y"}])
        assert out == {"fields": {"fldA": "y"}}

    def test_object_delete(self):
        doc = {"fields": {"fldA": "x"}}
        out = json0.apply(doc, [{"p": ["fields", "fldA"], "od": "x", "oi": None}])
        assert out == {"fields": {"fldA": None}}

    def test_number_add(self):
        doc = {"fields": {"n": 1}}
        out = json0.apply(doc, [{"p": ["fields", "n"], "na": 4}])
        assert out["fields"]["n"] == 5

    def test_string_insert_and_delete(self):
        doc = {"fields": {"s": "hi"}}
        out = json0.apply(doc, [{"p": ["fields", "s", 2], "si": "!"}])
        assert out["fields"]["s"] == "hi!"
        out2 = json0.apply(out, [{"p": ["fields", "s", 0], "sd": "hi"}])
        assert out2["fields"]["s"] == "!"

    def test_list_insert_delete_move(self):
        doc = {"tags": ["a", "c"]}
        out = json0.apply(doc, [{"p": ["tags", 1], "li": "b"}])
        assert out["tags"] == ["a", "b", "c"]
        out = json0.apply(out, [{"p": ["tags", 0], "ld": "a"}])
        assert out["tags"] == ["b", "c"]
        out = json0.apply(out, [{"p": ["tags", 0], "lm": 1}])
        assert out["tags"] == ["c", "b"]


class TestJson0Transform:
    def test_disjoint_paths_unchanged(self):
        a = [{"p": ["fields", "x"], "oi": 1}]
        b = [{"p": ["fields", "y"], "oi": 2}]
        assert json0.transform(a, b, "left") == a

    def test_concurrent_insert_right_yields(self):
        a = [{"p": ["fields", "x"], "oi": 1}]
        b = [{"p": ["fields", "x"], "oi": 2}]
        # right side drops its conflicting insert (left wins)
        assert json0.transform(a, b, "right") == []

    def test_concurrent_insert_left_keeps_with_delete(self):
        a = [{"p": ["fields", "x"], "oi": 1}]
        b = [{"p": ["fields", "x"], "oi": 2}]
        left = json0.transform(a, b, "left")
        assert left == [{"p": ["fields", "x"], "oi": 1, "od": 2}]

    def test_delete_against_other_delete_noops(self):
        a = [{"p": ["fields", "x"], "od": 1}]
        b = [{"p": ["fields", "x"], "od": 1}]
        assert json0.transform(a, b, "left") == []

    def test_invalid_side_rejected(self):
        with pytest.raises(json0.Json0Error):
            json0.transform([], [], "middle")


class TestSockjsFraming:
    def test_encode_array(self):
        # each element is an already-JSON-encoded message string; the array
        # frame quotes them again (double encoding, as SockJS expects)
        assert _encode_array(['{"a":"hs"}']) == 'a["{\\"a\\":\\"hs\\"}"]'

    def test_decode_array_of_message_strings(self):
        payload = '["{\\"a\\":\\"hs\\",\\"id\\":null}"]'
        out = SockJSConnection.decode_incoming(payload)
        assert out == [{"a": "hs", "id": None}]

    def test_decode_single_message(self):
        payload = '"{\\"a\\":\\"pp\\"}"'
        assert SockJSConnection.decode_incoming(payload) == [{"a": "pp"}]

    def test_decode_empty(self):
        assert SockJSConnection.decode_incoming("") == []


class _FakePubSub:
    async def subscribe(self, channel):  # pragma: no cover - unused here
        raise AssertionError("no subscribe expected")

    async def publish(self, channels, data):
        self.published = (channels, data)


def _agent():
    sent: list[dict] = []
    agent = Agent(sent.append, _FakePubSub(), {})
    return agent, sent


class TestShareDbRouting:
    def test_init_packet_sent_on_construction(self):
        _, sent = _agent()
        assert sent[0]["a"] == "init"
        assert sent[0]["protocol"] == 1
        assert sent[0]["type"] == "http://sharejs.org/types/JSONv0"

    def test_handshake_reply(self):
        agent, sent = _agent()
        asyncio.run(agent.handle_message({"a": "hs", "id": "clientsrc123", "protocol": 1}))
        assert agent.src == "clientsrc123"
        assert sent[-1]["a"] == "hs"
        assert sent[-1]["id"] == "clientsrc123"

    def test_ping_pong(self):
        agent, sent = _agent()
        asyncio.run(agent.handle_message({"a": "pp"}))
        assert sent[-1] == {"a": "pp"}

    def test_submit_non_record_rejected(self):
        agent, sent = _agent()
        asyncio.run(
            agent.handle_message({"a": "op", "c": "viw_tbl1", "d": "viw1", "op": [], "v": 1})
        )
        assert sent[-1]["error"]["message"] == "only record op can be committed"

    def test_submit_record_reports_rest_write_path(self):
        agent, sent = _agent()
        op = [{"p": ["fields", "f"], "oi": 1}]
        asyncio.run(
            agent.handle_message({"a": "op", "c": "rec_tbl1", "d": "rec1", "op": op, "v": 1})
        )
        # record writes flow through REST; the socket commit is unimplemented
        assert sent[-1]["error"]["code"] == 5019

    def test_doc_presence_json0_rejected(self):
        agent, sent = _agent()
        asyncio.run(
            agent.handle_message(
                {
                    "a": "p",
                    "ch": "rec_tbl1.rec1",
                    "id": "p1",
                    "p": {"x": 1},
                    "t": "http://sharejs.org/types/JSONv0",
                }
            )
        )
        assert sent[-1]["error"]["code"] == "ERR_TYPE_DOES_NOT_SUPPORT_PRESENCE"


class TestBuildSetRecordOp:
    def test_insert_when_old_none(self):
        assert build_set_record_op("fldA", "x", None) == {"p": ["fields", "fldA"], "oi": "x"}

    def test_replace(self):
        assert build_set_record_op("fldA", "y", "x") == {
            "p": ["fields", "fldA"],
            "od": "x",
            "oi": "y",
        }

    def test_delete_when_new_none(self):
        assert build_set_record_op("fldA", None, "x") == {
            "p": ["fields", "fldA"],
            "od": "x",
            "oi": None,
        }
