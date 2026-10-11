"""qs@6.13-compatible form decoding.

The reference backend registers ``express.urlencoded({extended: true})``
(express 4.21.1 → body-parser 1.20.3 → qs 6.13.x) and parses bodies with
``allowPrototypes: true``, ``arrayLimit: max(100, parameterCount)``,
``depth: 32``, ``parameterLimit: 1000`` and ``strictDepth: true``. This module
reproduces that parse so bracketed form fields reach the same Zod schemas with
the same shape (and the same failure modes) as the reference.

Behaviour notes (verified against the installed qs 6.13.1):

* ``%5B``/``%5D`` are rewritten to literal brackets before splitting; a key
  without a value is ``''`` and an empty key is skipped entirely.
* duplicate keys combine into arrays; merging a scalar into an object writes
  ``obj[scalar] = true`` (qs ``allowPrototypes``), and merging into an array
  appends.
* keys split on non-nested bracket groups only, so ``a[[b]]`` is not a nested
  path there; more than ``depth`` groups raise the strictDepth error.
* numeric-looking keys become array indices only when canonical
  (``a[01]`` stays a key) and ``index <= arrayLimit``.
* invalid percent-encoding keeps the raw text (qs 6.13 does not fall back to
  latin-1 byte decoding).
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote

DEFAULT_DEPTH = 32
DEFAULT_PARAMETER_LIMIT = 1000
DEFAULT_ARRAY_LIMIT = 100

HOLE = object()
"""Sparse-array placeholder, standing in for an absent index."""


class QsDepthError(ValueError):
    """strictDepth violation: body-parser reports this as a 400."""


class QsParameterLimitError(ValueError):
    """parameterLimit violation: body-parser reports this as a 413."""


_MALFORMED_PERCENT = re.compile(r"%(?![0-9a-fA-F]{2})")
_BRACKET_OPEN = re.compile(r"%5B", re.IGNORECASE)
_BRACKET_CLOSE = re.compile(r"%5D", re.IGNORECASE)
_BRACKET_GROUP = re.compile(r"(\[[^[\]]*])")
_JS_ARRAY_INDEX = re.compile(r"^(0|[1-9][0-9]*)$")


def _js_keys(mapping: dict[str, Any]) -> list[str]:
    """Object.keys order: integer-like keys ascending first, then insertion order."""
    indexed: list[tuple[int, str]] = []
    rest: list[str] = []
    for key in mapping:
        if _JS_ARRAY_INDEX.match(key) and int(key) < 2**32 - 1:
            indexed.append((int(key), key))
        else:
            rest.append(key)
    indexed.sort()
    return [key for _, key in indexed] + rest


def _has(values: list[Any], index: int) -> bool:
    return index < len(values) and values[index] is not HOLE


def decode(value: str) -> str:
    """qs 6.13 decode(): '+'-to-space, UTF-8, raw text when the encoding is bad."""
    value = value.replace("+", " ")
    try:
        if _MALFORMED_PERCENT.search(value):
            raise UnicodeDecodeError("utf-8", b"", 0, 1, "malformed percent")
        return unquote(value, encoding="utf-8", errors="strict")
    except UnicodeDecodeError:
        return value


def _array_to_object(source: list[Any]) -> dict[str, Any]:
    return {
        str(index): value
        for index, value in enumerate(source)
        if value is not HOLE and value is not None
    }


def _concat(first: Any, second: Any) -> list[Any]:
    """JavaScript ``[].concat(a, b)``: arrays spread, everything else appended."""
    left = list(first) if isinstance(first, list) else [first]
    right = list(second) if isinstance(second, list) else [second]
    return [*left, *right]


def _combine(a: Any, b: Any) -> list[Any]:
    return _concat(a, b)


def _merge(target: Any, source: Any) -> Any:
    # qs merge() bails on falsy sources: `a[b]=1` followed by `a=` keeps the first.
    if source is None or source == "":
        return target

    if not isinstance(source, (list, dict)):
        if isinstance(target, list):
            target.append(source)
            return target
        if isinstance(target, dict):
            # allowPrototypes: true → obj[source] = true
            target[str(source)] = True
            return target
        return [target, source]

    if not isinstance(target, (list, dict)):
        return _concat(target, source)

    if isinstance(target, list) and not isinstance(source, list):
        target = _array_to_object(target)

    if isinstance(target, list) and isinstance(source, list):
        for index, item in enumerate(source):
            if item is HOLE:
                continue
            if _has(target, index) and isinstance(target[index], (list, dict)) and isinstance(
                item, (list, dict)
            ):
                target[index] = _merge(target[index], item)
            elif _has(target, index):
                target.append(item)
            else:
                while len(target) < index:
                    target.append(HOLE)
                target.append(item)
        return target

    assert isinstance(target, dict)
    source_map: dict[str, Any] = (
        {str(index): item for index, item in enumerate(source) if item is not HOLE}
        if isinstance(source, list)
        else source
    )
    for key in _js_keys(source_map):
        if key in target:
            target[key] = _merge(target[key], source_map[key])
        else:
            target[key] = source_map[key]
    return target


def _parse_js_int(text: str) -> int | None:
    """JavaScript parseInt(text, 10): prefix digits, plus sign, Infinity for huge."""
    match = re.match(r"^[ \t\n\r]*([+-]?)([0-9]+)", text)
    if not match:
        return None
    value = int(match.group(2))
    return -value if match.group(1) == "-" else value


def _split_keys(key: str, depth: int) -> list[str]:
    """qs 6.13 parseKeys: non-nested bracket groups, capped at ``depth``."""
    first = _BRACKET_GROUP.search(key) if depth > 0 else None
    parent = key[: first.start()] if first else key
    segments: list[str] = []
    if parent:
        segments.append(parent)
    consumed = 0
    index = 0
    matched = first
    while depth > 0 and matched is not None and consumed < depth:
        consumed += 1
        segments.append(matched.group(1))
        index = matched.end()
        matched = _BRACKET_GROUP.search(key, index)
    if matched is not None:
        raise QsDepthError(
            f"Input depth exceeded depth option of {depth} and strictDepth is true"
        )
    return segments


def _parse_object(chain: list[str], value: Any, array_limit: int) -> Any:
    leaf = value
    for root in reversed(chain):
        if root == "[]":
            obj: Any = _concat([], leaf)
        else:
            clean_root = root[1:-1] if root.startswith("[") and root.endswith("]") else root
            if clean_root == "__proto__":
                leaf = {}
                continue
            index = _parse_js_int(clean_root)
            if (
                root != clean_root
                and index is not None
                and index >= 0
                and str(index) == clean_root
                and index <= array_limit
            ):
                obj = [HOLE] * (index + 1)
                obj[index] = leaf
            else:
                obj = {clean_root: leaf}
        leaf = obj
    return leaf


def parse(
    text: str,
    *,
    parameter_limit: int = DEFAULT_PARAMETER_LIMIT,
    depth: int = DEFAULT_DEPTH,
    array_limit: int | None = None,
) -> dict[str, Any]:
    """Parse an ``application/x-www-form-urlencoded`` body like qs/body-parser."""
    if text == "":
        return {}
    clean = _BRACKET_CLOSE.sub("]", _BRACKET_OPEN.sub("[", text))
    parts = clean.split("&", parameter_limit)
    limit = array_limit if array_limit is not None else max(DEFAULT_ARRAY_LIMIT, len(parts))
    values: dict[str, Any] = {}
    for part in parts:
        bracket_equals = part.find("]=")
        position = bracket_equals + 1 if bracket_equals >= 0 else part.find("=")
        if position == -1:
            key, raw_value = decode(part), ""
        else:
            key, raw_value = decode(part[:position]), decode(part[position + 1 :])
        if not key:
            continue
        value: Any = raw_value
        if "[]=" in part and isinstance(value, list):
            value = [value]
        values[key] = _combine(values[key], value) if key in values else value
    result: dict[str, Any] = {}
    for key in _js_keys(values):
        segments = _split_keys(key, depth)
        if not segments:
            continue
        result = _merge(result, _parse_object(segments, values[key], limit))
    return _compact(result)


def count_parameters(text: str, limit: int = DEFAULT_PARAMETER_LIMIT) -> int:
    """body-parser parameterCount(): raises when the body exceeds the limit."""
    count = text.count("&") + 1 if text else 0
    if count > limit:
        raise QsParameterLimitError("too many parameters")
    return count


def _compact(value: Any) -> Any:
    if isinstance(value, list):
        return [_compact(item) for item in value if item is not HOLE]
    if isinstance(value, dict):
        for key in list(value):
            value[key] = _compact(value[key])
    return value
