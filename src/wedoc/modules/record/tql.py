"""TQL subset parser — ports the reference parseTQL (filter-conv).

Supported grammar (enough for the open-api surface):
    expr    := orExpr
    orExpr  := andExpr (OR andExpr)*
    andExpr := factor (AND factor)*
    factor  := '(' expr ')' | predicate
    predicate := '{' name '}' op literal | '{' name '}' IS [NOT] NULL
    op      := '=' | '!=' | '<>' | '>' | '>=' | '<' | '<='
    literal := number | 'string' | "string"

Output is the canonical filter JSON (filterSet/conjunction/operator names)
consumed by the record query compiler. Keywords are case-insensitive; field
refs resolve by name or id later (unknown refs drop the whole filter, as the
reference implementation silently does).
"""

import re
from dataclasses import dataclass
from typing import Any


class TqlParseError(ValueError):
    pass


@dataclass
class _Token:
    kind: str
    value: str


_TOKEN_RE = re.compile(
    r"""
    (?P<space>\s+)
  | (?P<field>\{[^{}]*\})
  | (?P<squote>'(?:[^'\\]|\\.)*')
  | (?P<dquote>"(?:[^"\\]|\\.)*")
  | (?P<number>-?\d+(?:\.\d+)?)
  | (?P<op><=|>=|!=|<>|=|<|>)
  | (?P<lpar>\()
  | (?P<rpar>\))
  | (?P<ident>[A-Za-z_][A-Za-z0-9_]*)
""",
    re.VERBOSE,
)

_KEYWORDS = {"and": "AND", "or": "OR", "is": "IS", "not": "NOT", "null": "NULL"}


def _tokenize(text: str) -> list[_Token]:
    tokens: list[_Token] = []
    pos = 0
    while pos < len(text):
        match = _TOKEN_RE.match(text, pos)
        if not match:
            raise TqlParseError(f"invalid token: {text[pos:pos + 8]!r}")
        pos = match.end()
        kind = match.lastgroup or ""
        if kind == "space":
            continue
        value = match.group()
        if kind == "ident":
            upper = value.upper()
            if upper in ("AND", "OR", "IS", "NULL"):
                tokens.append(_Token(upper, value))
                continue
            if upper == "NOT":
                tokens.append(_Token("NOT", value))
                continue
            raise TqlParseError(f"invalid token: {value!r}")
        tokens.append(_Token(kind.upper(), value))
    tokens.append(_Token("EOF", ""))
    return tokens


_OPS = {
    "=": "is",
    "!=": "isNot",
    "<>": "isNot",
    ">": "isGreater",
    ">=": "isGreaterEqual",
    "<": "isLess",
    "<=": "isLessEqual",
}


class _Parser:
    def __init__(self, tokens: list[_Token]) -> None:
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> _Token:
        return self.tokens[self.pos]

    def next(self) -> _Token:
        token = self.tokens[self.pos]
        self.pos += 1
        return token

    def expect(self, kind: str) -> _Token:
        token = self.peek()
        if token.kind != kind:
            raise TqlParseError(f"expected {kind}, got {token.value!r}")
        return self.next()

    def parse_expr(self) -> dict[str, Any]:
        return self.parse_or()

    def parse_or(self) -> dict[str, Any]:
        parts = [self.parse_and()]
        while self.peek().kind == "OR":
            self.next()
            parts.append(self.parse_and())
        if len(parts) == 1:
            return parts[0]
        return {"conjunction": "or", "filterSet": parts}

    def parse_and(self) -> dict[str, Any]:
        parts = [self.parse_factor()]
        while self.peek().kind == "AND":
            self.next()
            parts.append(self.parse_factor())
        if len(parts) == 1:
            return parts[0]
        return {"conjunction": "and", "filterSet": parts}

    def parse_factor(self) -> dict[str, Any]:
        if self.peek().kind == "LPAR":
            self.next()
            expr = self.parse_expr()
            self.expect("RPAR")
            return expr
        return self.parse_predicate()

    def parse_predicate(self) -> dict[str, Any]:
        field = self.expect("FIELD").value[1:-1].strip()
        if self.peek().kind == "IS":
            self.next()
            negate = False
            if self.peek().kind == "NOT":
                self.next()
                negate = True
            self.expect("NULL")
            return {
                "fieldId": field,
                "operator": "isNot" if negate else "is",
                "value": None,
            }
        token = self.peek()
        if token.kind != "OP":
            raise TqlParseError(f"expected comparison operator, got {token.value!r}")
        self.next()
        operator = _OPS[token.value]
        literal = self.peek()
        if literal.kind == "NUMBER":
            self.next()
            value: Any = float(literal.value) if "." in literal.value else int(literal.value)
        elif literal.kind in ("SQUOTE", "DQUOTE"):
            self.next()
            value = literal.value[1:-1]
        else:
            raise TqlParseError(f"expected literal, got {literal.value!r}")
        return {"fieldId": field, "operator": operator, "value": value}


def parse_tql(text: str) -> dict[str, Any]:
    """Parse a TQL string into the canonical filter JSON."""
    result = _Parser(_tokenize(text)).parse_expr()
    if "filterSet" not in result:
        # a lone predicate still travels as a filter object downstream
        result = {"conjunction": "and", "filterSet": [result]}
    return result
