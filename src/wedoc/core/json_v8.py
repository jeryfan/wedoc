"""V8-compatible ``JSON.parse``.

The reference backend parses JSON bodies with Node's ``JSON.parse`` inside
body-parser. Malformed bodies therefore surface V8's own wording, e.g.
``Expected property name or '}' in JSON at position 1 (line 1 column 2)``.
Re-using Python's ``json`` module would emit different messages, so this module
reproduces V8's parser, position/line/column reporting and truncation rules.

Also reproduces body-parser's ``strict`` pre-check (the first non-whitespace
character must be ``{`` or ``[``) including its "mask and reparse" trick that
produces ``Unexpected token 'n', "null" is not valid JSON``.
"""

from __future__ import annotations

import re
from typing import Any

_JSON_WS = " \t\n\r"
_DIGITS = "0123456789"
_HEX = "0123456789abcdefABCDEF"
_ESCAPES = {
    '"': '"',
    "\\": "\\",
    "/": "/",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
}
_FIRST_CHAR = re.compile(r"[ \t\n\r]*([^ \t\n\r])")
_HASH_RUN = re.compile(r"#+")

_SNIPPET_LIMIT = 20
_SNIPPET_LENGTH = 10


class JsonSyntaxError(ValueError):
    """A parse failure whose message matches Node's ``JSON.parse``."""


class _Parser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0

    # -- error helpers -----------------------------------------------------

    def _u16(self, position: int) -> int:
        return len(self.text[:position].encode("utf-16-le", errors="surrogatepass")) // 2

    def _at(self, message: str, position: int) -> JsonSyntaxError:
        offset = self._u16(position)
        prefix = self.text[:position]
        newline = prefix.rfind("\n")
        if newline >= 0:
            line = prefix.count("\n") + 1
            column = offset - self._u16(newline + 1) + 1
        else:
            line = 1
            column = offset + 1
        # V8 omits "in JSON" for the trailing-data message.
        connector = "" if message.endswith("after JSON") else " in JSON"
        return JsonSyntaxError(
            f"{message}{connector} at position {offset} (line {line} column {column})"
        )

    def _unexpected_token(self, position: int) -> JsonSyntaxError:
        if position >= len(self.text):
            return JsonSyntaxError("Unexpected end of JSON input")
        char = self.text[position]
        if len(self.text) <= _SNIPPET_LIMIT:
            snippet = self.text
            return JsonSyntaxError(f"Unexpected token '{char}', \"{snippet}\" is not valid JSON")
        snippet = self.text[:_SNIPPET_LENGTH]
        return JsonSyntaxError(f"Unexpected token '{char}', \"{snippet}\"... is not valid JSON")

    # -- scanner -----------------------------------------------------------

    def skip_ws(self) -> None:
        while self.pos < len(self.text) and self.text[self.pos] in _JSON_WS:
            self.pos += 1

    def parse_document(self) -> Any:
        self.skip_ws()
        if self.pos >= len(self.text):
            raise JsonSyntaxError("Unexpected end of JSON input")
        value = self.parse_value()
        self.skip_ws()
        if self.pos < len(self.text):
            raise self._at("Unexpected non-whitespace character after JSON", self.pos)
        return value

    def parse_value(self) -> Any:
        if self.pos >= len(self.text):
            raise JsonSyntaxError("Unexpected end of JSON input")
        char = self.text[self.pos]
        if char == "{":
            return self.parse_object()
        if char == "[":
            return self.parse_array()
        if char == '"':
            return self.parse_string()
        if char == "-" or char in _DIGITS:
            return self.parse_number()
        if char == "t":
            return self.parse_literal("true", True)
        if char == "f":
            return self.parse_literal("false", False)
        if char == "n":
            return self.parse_literal("null", None)
        raise self._unexpected_token(self.pos)

    def parse_literal(self, word: str, value: Any) -> Any:
        for expected in word:
            if self.pos >= len(self.text):
                raise JsonSyntaxError("Unexpected end of JSON input")
            char = self.text[self.pos]
            if char != expected:
                if char in _DIGITS:
                    raise self._at("Unexpected number", self.pos)
                if char == '"':
                    raise self._at("Unexpected string", self.pos)
                raise self._unexpected_token(self.pos)
            self.pos += 1
        return value

    def parse_object(self) -> dict[str, Any]:
        self.pos += 1
        result: dict[str, Any] = {}
        self.skip_ws()
        if self.pos < len(self.text) and self.text[self.pos] == "}":
            self.pos += 1
            return result
        first = True
        while True:
            if self.pos >= len(self.text):
                if first:
                    raise self._at("Expected property name or '}'", self.pos)
                raise self._at("Expected double-quoted property name", self.pos)
            if self.text[self.pos] != '"':
                if first:
                    raise self._at("Expected property name or '}'", self.pos)
                raise self._at("Expected double-quoted property name", self.pos)
            key = self.parse_string()
            self.skip_ws()
            if self.pos >= len(self.text) or self.text[self.pos] != ":":
                raise self._at("Expected ':' after property name", self.pos)
            self.pos += 1
            self.skip_ws()
            result[key] = self.parse_value()
            self.skip_ws()
            if self.pos >= len(self.text) or self.text[self.pos] not in ",}":
                raise self._at("Expected ',' or '}' after property value", self.pos)
            if self.text[self.pos] == "}":
                self.pos += 1
                return result
            self.pos += 1
            self.skip_ws()
            first = False

    def parse_array(self) -> list[Any]:
        self.pos += 1
        result: list[Any] = []
        self.skip_ws()
        if self.pos < len(self.text) and self.text[self.pos] == "]":
            self.pos += 1
            return result
        while True:
            result.append(self.parse_value())
            self.skip_ws()
            if self.pos >= len(self.text) or self.text[self.pos] not in ",]":
                raise self._at("Expected ',' or ']' after array element", self.pos)
            if self.text[self.pos] == "]":
                self.pos += 1
                return result
            self.pos += 1
            self.skip_ws()

    def parse_string(self) -> str:
        self.pos += 1
        units: list[int] = []
        while True:
            if self.pos >= len(self.text):
                raise self._at("Unterminated string", self.pos)
            char = self.text[self.pos]
            if char == '"':
                self.pos += 1
                return _join_units(units)
            if char == "\\":
                self.pos += 1
                if self.pos >= len(self.text):
                    raise self._at("Unterminated string", self.pos)
                escape = self.text[self.pos]
                if escape in _ESCAPES:
                    units.append(ord(_ESCAPES[escape]))
                elif escape == "u":
                    self.pos += 1
                    digits = ""
                    for _ in range(4):
                        if self.pos >= len(self.text) or self.text[self.pos] not in _HEX:
                            raise self._at("Bad Unicode escape", self.pos)
                        digits += self.text[self.pos]
                        self.pos += 1
                    units.append(int(digits, 16))
                    continue
                else:
                    raise self._at("Bad escaped character", self.pos)
                self.pos += 1
            elif ord(char) < 0x20:
                raise self._at("Bad control character in string literal", self.pos)
            else:
                units.append(ord(char))
                self.pos += 1

    def parse_number(self) -> int | float:
        start = self.pos
        if self.text[self.pos] == "-":
            self.pos += 1
            if self.pos >= len(self.text) or self.text[self.pos] not in _DIGITS:
                raise self._at("No number after minus sign", self.pos)
        if self.text[self.pos] == "0":
            self.pos += 1
            if self.pos < len(self.text) and self.text[self.pos] in _DIGITS:
                raise self._at("Unexpected number", self.pos)
        else:
            while self.pos < len(self.text) and self.text[self.pos] in _DIGITS:
                self.pos += 1
        fractional = False
        if self.pos < len(self.text) and self.text[self.pos] == ".":
            fractional = True
            self.pos += 1
            if self.pos >= len(self.text) or self.text[self.pos] not in _DIGITS:
                raise self._at("Unterminated fractional number", self.pos)
            while self.pos < len(self.text) and self.text[self.pos] in _DIGITS:
                self.pos += 1
        if self.pos < len(self.text) and self.text[self.pos] in "eE":
            fractional = True
            self.pos += 1
            if self.pos < len(self.text) and self.text[self.pos] in "+-":
                self.pos += 1
            if self.pos >= len(self.text) or self.text[self.pos] not in _DIGITS:
                raise self._at("Exponent part is missing a number", self.pos)
            while self.pos < len(self.text) and self.text[self.pos] in _DIGITS:
                self.pos += 1
        literal = self.text[start : self.pos]
        if not fractional:
            # JavaScript numbers are doubles; beyond 2**53-1 the int would be
            # more precise than the reference, so fall back to the double value.
            value = int(literal)
            if abs(value) <= 2**53 - 1:
                return value
        return float(literal)


def _join_units(units: list[int]) -> str:
    """Join UTF-16 code units, combining surrogate pairs the way JS strings do."""
    out: list[str] = []
    index = 0
    while index < len(units):
        unit = units[index]
        if (
            0xD800 <= unit <= 0xDBFF
            and index + 1 < len(units)
            and 0xDC00 <= units[index + 1] <= 0xDFFF
        ):
            out.append(chr(0x10000 + ((unit - 0xD800) << 10) + (units[index + 1] - 0xDC00)))
            index += 2
            continue
        out.append(chr(unit))
        index += 1
    return "".join(out)


def parse(text: str) -> Any:
    """``JSON.parse`` with V8's error messages."""
    return _Parser(text).parse_document()


def strict_message(text: str) -> str | None:
    """body-parser ``strict`` violation message, or None when the body is fine."""
    match = _FIRST_CHAR.search(text)
    first = match.group(1) if match else None
    if first == "{" or first == "[":
        return None
    # Mirrors body-parser createStrictSyntaxError: mask everything from the
    # first character on, parse the mask, then substitute the real text back.
    index = text.find(first) if first is not None else -1
    if index < 0:
        try:
            parse("")
            return "strict violation"
        except JsonSyntaxError as exc:
            return str(exc)
    masked = text[:index] + "#" * (len(text) - index)
    try:
        parse(masked)
        return "strict violation"
    except JsonSyntaxError as exc:
        message = str(exc)
    return _HASH_RUN.sub(lambda m: text[index : index + len(m.group(0))], message)
