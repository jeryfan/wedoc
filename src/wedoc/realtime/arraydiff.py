"""Ordered-list diff matching arraydiff 0.1.3, used by the ShareDB query emitter.

Given the previous and next ordered id lists of a query result set, produce the
remove/move/insert operations that transform one into the other, in the wire
shape ShareDB emits inside a ``q`` (queryUpdate) diff. Insert ``values`` carry
the matched ids; the caller swaps them for snapshot payloads before sending.
"""

from __future__ import annotations

from typing import Any


def array_diff(before: list[Any], after: list[Any]) -> list[dict[str, Any]]:
    before_length = len(before)
    after_length = len(after)
    moves: list[dict[str, Any]] = []
    before_marked: dict[int, bool] = {}
    after_marked: dict[int, bool] = {}

    before_index = 0
    while before_index < before_length:
        before_item = before[before_index]
        after_index = 0
        while after_index < after_length:
            if after_marked.get(after_index) or before_item != after[after_index]:
                after_index += 1
                continue
            from_index = before_index
            to_index = after_index
            how_many = 0
            while True:
                before_marked[before_index] = True
                after_marked[after_index] = True
                before_index += 1
                after_index += 1
                how_many += 1
                if not (
                    before_index < before_length
                    and after_index < after_length
                    and before[before_index] == after[after_index]
                    and not after_marked.get(after_index)
                ):
                    break
            moves.append({"type": "move", "from": from_index, "to": to_index, "howMany": how_many})
            before_index -= 1
            break
        before_index += 1

    removes: list[dict[str, Any]] = []
    before_index = 0
    while before_index < before_length:
        if before_marked.get(before_index):
            before_index += 1
            continue
        index = before_index
        how_many = 0
        while before_index < before_length:
            marked = before_marked.get(before_index)
            before_index += 1
            if marked:
                break
            how_many += 1
        removes.append({"type": "remove", "index": index, "howMany": how_many})

    inserts: list[dict[str, Any]] = []
    after_index = 0
    while after_index < after_length:
        if after_marked.get(after_index):
            after_index += 1
            continue
        index = after_index
        how_many = 0
        while after_index < after_length:
            marked = after_marked.get(after_index)
            after_index += 1
            if marked:
                break
            how_many += 1
        inserts.append(
            {"type": "insert", "index": index, "values": after[index : index + how_many]}
        )

    moves_length = len(moves)

    count = 0
    for remove in removes:
        remove["index"] -= count
        count += remove["howMany"]
        for move in moves:
            if move["from"] >= remove["index"]:
                move["from"] -= remove["howMany"]

    for i in range(len(inserts) - 1, -1, -1):
        insert = inserts[i]
        how_many = len(insert["values"])
        for j in range(moves_length - 1, -1, -1):
            move = moves[j]
            if move["to"] >= insert["index"]:
                move["to"] -= how_many

    for i in range(moves_length - 1, 0, -1):
        move = moves[i]
        if move["to"] == move["from"]:
            continue
        for j in range(i - 1, -1, -1):
            earlier = moves[j]
            if earlier["to"] >= move["to"]:
                earlier["to"] -= move["howMany"]
            if earlier["to"] >= move["from"]:
                earlier["to"] += move["howMany"]

    output_moves: list[dict[str, Any]] = []
    for i in range(moves_length):
        move = moves[i]
        if move["to"] == move["from"]:
            continue
        output_moves.append(move)
        for j in range(i + 1, moves_length):
            later = moves[j]
            if later["from"] >= move["from"]:
                later["from"] -= move["howMany"]
            if later["from"] >= move["to"]:
                later["from"] += move["howMany"]

    return removes + output_moves + inserts
