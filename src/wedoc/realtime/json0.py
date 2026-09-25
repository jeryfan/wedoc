"""Minimal json0 OT type, matching the subset of ot-json0 that record docs use.

Record collaboration ops are object mutations on the ``fields`` map plus the
occasional numeric/text/list tweak, so this implements the object (``oi``/``od``),
number (``na``), string (``si``/``sd``) and list (``li``/``ld``/``lm``) components
of json0. The wire shape of each component matches ot-json0 exactly:

- ``{"p": path, "oi": v}``            object insert
- ``{"p": path, "od": v}``            object delete
- ``{"p": path, "od": old, "oi": v}`` object replace
- ``{"p": path, "na": n}``            number add
- ``{"p": path, "li": v}``            list insert
- ``{"p": path, "ld": v}``            list delete
- ``{"p": path, "ld": v, "li": w}``   list replace
- ``{"p": path, "lm": index}``        list move
- ``{"p": [...,offset], "si": s}``    string insert
- ``{"p": [...,offset], "sd": s}``    string delete

``apply`` mutates a deep copy so callers keep the original snapshot. ``transform``
implements the OT transform used when two clients edit the same doc concurrently.
"""

from __future__ import annotations

import copy
from typing import Any

Op = dict[str, Any]
Path = list[Any]


class Json0Error(ValueError):
    """Raised when an op cannot be applied to the given snapshot."""


def _navigate(data: Any, path: Path) -> tuple[Any, Any]:
    """Return (container, key) where container[key] is the path target."""
    container = data
    for key in path[:-1]:
        container = container[key]
    return container, path[-1]


def apply(snapshot: Any, ops: list[Op]) -> Any:
    """Apply a json0 op list to a snapshot, returning the new snapshot."""
    doc = copy.deepcopy(snapshot)
    for op in ops:
        doc = _apply_component(doc, op)
    return doc


def _apply_component(doc: Any, op: Op) -> Any:
    path: Path = list(op.get("p", []))
    if not path:
        if "oi" in op:
            return op["oi"]
        if "od" in op:
            return None
        raise Json0Error(f"unsupported root op: {op}")

    if "si" in op or "sd" in op:
        _apply_string(doc, path, op)
        return doc

    container, key = _navigate(doc, path)

    if "na" in op:
        base = container[key] if _has(container, key) else 0
        container[key] = base + op["na"]
        return doc
    if "lm" in op:
        value = container.pop(key)
        container.insert(op["lm"], value)
        return doc
    if "li" in op and "ld" in op:
        container[key] = op["li"]
        return doc
    if "li" in op:
        container.insert(key, op["li"])
        return doc
    if "ld" in op:
        del container[key]
        return doc
    if "oi" in op:
        container[key] = op["oi"]
        return doc
    if "od" in op:
        if isinstance(container, dict):
            container.pop(key, None)
        else:
            del container[key]
        return doc
    raise Json0Error(f"unsupported op component: {op}")


def _has(container: Any, key: Any) -> bool:
    if isinstance(container, dict):
        return key in container
    return isinstance(key, int) and 0 <= key < len(container)


def _apply_string(doc: Any, path: Path, op: Op) -> None:
    offset = path[-1]
    str_path = path[:-1]
    if not str_path:
        raise Json0Error("string op needs a parent path")
    parent, key = _navigate(doc, str_path)
    current = parent[key] or ""
    if "si" in op:
        parent[key] = current[:offset] + op["si"] + current[offset:]
    else:
        deleted = op["sd"]
        parent[key] = current[:offset] + current[offset + len(deleted) :]


# ---- transform -------------------------------------------------------------


def _common_prefix(path: Path, prefix: Path) -> bool:
    if len(prefix) > len(path):
        return False
    return path[: len(prefix)] == prefix


def transform(op: list[Op], other: list[Op], side: str) -> list[Op]:
    """Transform ``op`` so it applies after ``other``; ``side`` breaks ties."""
    if side not in ("left", "right"):
        raise Json0Error(f"side must be 'left' or 'right', got {side!r}")
    result: list[Op] = []
    for component in op:
        transformed: list[Op] = [component]
        for other_component in other:
            next_round: list[Op] = []
            for c in transformed:
                next_round.extend(_transform_component(c, other_component, side))
            transformed = next_round
            if not transformed:
                break
        result.extend(transformed)
    return result


def _transform_component(c: Op, o: Op, side: str) -> list[Op]:
    cp: Path = list(c.get("p", []))
    op_: Path = list(o.get("p", []))

    # Disjoint subtrees never interfere.
    if not _common_prefix(cp, op_) and not _common_prefix(op_, cp):
        return [c]

    if cp == op_:
        # Other deleted the node we also target.
        if "od" in o and "oi" not in o:
            if "oi" in c and "od" in c:
                return [{"p": cp, "oi": c["oi"]}]
            if "od" in c and "oi" not in c:
                return []
            return [c]
        # Other wrote a value where we also write; right yields to left.
        if "oi" in o and ("oi" in c or "od" in c):
            if side == "right":
                new_c = dict(c)
                if "od" in new_c:
                    new_c["od"] = o["oi"]
                elif "oi" in new_c:
                    # a plain insert on the right loses to the left's insert
                    return []
                return [new_c]
            new_c = dict(c)
            if "oi" in new_c and "od" not in new_c:
                new_c["od"] = o["oi"]
            return [new_c]
        # Concurrent numeric adds both commute.
        if "na" in o and "na" in c:
            return [c]
    return [c]
