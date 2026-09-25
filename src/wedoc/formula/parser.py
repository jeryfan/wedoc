"""Hand-written lexer + Pratt parser for the formula grammar.

Ports packages/formula Formula.g4 / FormulaLexer.g4: literals, curly field
references ``{fldXxx}``, unary minus, the binary operator set and function
calls. Operator precedence follows the grammar's alternative ordering (``&``
binds loosest, then ``||``, ``&&``, equality, relational, additive,
multiplicative, unary). ``<>`` is accepted as an alias of ``!=`` (grammar has
the token but not the rule); this is a documented superset.
"""

from __future__ import annotations

from dataclasses import dataclass


class FormulaError(Exception):
    """Raised for lex/parse failures and evaluation type errors."""


@dataclass(frozen=True)
class Num:
    value: int | float


@dataclass(frozen=True)
class Str:
    value: str


@dataclass(frozen=True)
class Bool:
    value: bool


@dataclass(frozen=True)
class FieldRef:
    id: str


@dataclass(frozen=True)
class Neg:
    operand: object


@dataclass(frozen=True)
class Bin:
    op: str
    left: object
    right: object


@dataclass(frozen=True)
class Call:
    name: str
    args: tuple


Node = Num | Str | Bool | FieldRef | Neg | Bin | Call

_ESCAPES = {
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "b": "\b",
    "f": "\f",
    "v": "\v",
    "\\": "\\",
    '"': '"',
    "'": "'",
}

def _unescape(raw: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(raw):
        ch = raw[i]
        if ch == "\\" and i + 1 < len(raw):
            nxt = raw[i + 1]
            out.append(_ESCAPES.get(nxt, "\\" + nxt))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _is_ident_start(ch: str) -> bool:
    return ch.isalpha() or ch == "_" or ord(ch) >= 0xA1


def _is_ident_part(ch: str) -> bool:
    return _is_ident_start(ch) or ch.isdigit()


_OPS_2 = ("&&", "||", "!=", "<>", ">=", "<=")
_OPS_1 = set("+-*/%&=<>")

# TOKENS_MARKER


def _tokenize(src: str) -> list[tuple[str, object]]:
    tokens: list[tuple[str, object]] = []
    i = 0
    n = len(src)
    while i < n:
        ch = src[i]
        if ch in " \t\r\n":
            i += 1
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "/":
            i += 2
            while i < n and src[i] not in "\r\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "*":
            end = src.find("*/", i + 2)
            if end == -1:
                raise FormulaError("unterminated block comment")
            i = end + 2
            continue
        if ch == "{":
            end = src.find("}", i + 1)
            if end == -1:
                raise FormulaError("unterminated field reference")
            tokens.append(("field", src[i + 1 : end].strip()))
            i = end + 1
            continue
        if ch in "'\"":
            j = i + 1
            buf: list[str] = []
            while j < n:
                cj = src[j]
                if cj == "\\" and j + 1 < n:
                    buf.append(src[j : j + 2])
                    j += 2
                    continue
                if cj == ch:
                    break
                buf.append(cj)
                j += 1
            if j >= n:
                raise FormulaError("unterminated string literal")
            tokens.append(("string", _unescape("".join(buf))))
            i = j + 1
            continue
        if ch.isdigit():
            i = _read_number(src, i, tokens)
            continue
        if _is_ident_start(ch):
            j = i + 1
            while j < n and _is_ident_part(src[j]):
                j += 1
            word = src[i:j]
            upper = word.upper()
            if upper == "TRUE":
                tokens.append(("bool", True))
            elif upper == "FALSE":
                tokens.append(("bool", False))
            else:
                tokens.append(("ident", word))
            i = j
            continue
        two = src[i : i + 2]
        if two in _OPS_2:
            tokens.append(("op", "!=" if two == "<>" else two))
            i += 2
            continue
        if ch in "()":
            tokens.append(("lparen" if ch == "(" else "rparen", ch))
            i += 1
            continue
        if ch == ",":
            tokens.append(("comma", ch))
            i += 1
            continue
        if ch in _OPS_1:
            tokens.append(("op", ch))
            i += 1
            continue
        raise FormulaError(f"unexpected character {ch!r}")
    tokens.append(("eof", None))
    return tokens


def _read_number(src: str, i: int, tokens: list[tuple[str, object]]) -> int:
    n = len(src)
    j = i
    while j < n and src[j].isdigit():
        j += 1
    is_float = False
    if j < n and src[j] == "." and j + 1 < n and src[j + 1].isdigit():
        is_float = True
        j += 1
        while j < n and src[j].isdigit():
            j += 1
    if j < n and src[j] in "eE":
        k = j + 1
        if k < n and src[k] == "-":
            k += 1
        if k < n and src[k].isdigit():
            is_float = True
            j = k
            while j < n and src[j].isdigit():
                j += 1
    text = src[i:j]
    tokens.append(("number", float(text) if is_float else int(text)))
    return j


# PARSER_MARKER

_LBP = {
    "&": 10,
    "||": 20,
    "&&": 30,
    "=": 40,
    "!=": 40,
    ">": 50,
    ">=": 50,
    "<": 50,
    "<=": 50,
    "+": 60,
    "-": 60,
    "*": 70,
    "/": 70,
    "%": 70,
}
_UNARY_RBP = 75


class _Parser:
    def __init__(self, tokens: list[tuple[str, object]]) -> None:
        self.tokens = tokens
        self.pos = 0

    def _peek(self) -> tuple[str, object]:
        return self.tokens[self.pos]

    def _next(self) -> tuple[str, object]:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def parse(self) -> Node:
        node = self._expr(0)
        kind, _ = self._peek()
        if kind != "eof":
            raise FormulaError("unexpected trailing input")
        return node

    def _expr(self, rbp: int) -> Node:
        left = self._prefix()
        while True:
            kind, value = self._peek()
            if kind != "op":
                break
            lbp = _LBP.get(value)  # type: ignore[arg-type]
            if lbp is None or lbp <= rbp:
                break
            self._next()
            right = self._expr(lbp)
            left = Bin(value, left, right)  # type: ignore[arg-type]
        return left

    def _prefix(self) -> Node:
        kind, value = self._next()
        if kind == "number":
            return Num(value)  # type: ignore[arg-type]
        if kind == "string":
            return Str(value)  # type: ignore[arg-type]
        if kind == "bool":
            return Bool(value)  # type: ignore[arg-type]
        if kind == "field":
            if not value:
                raise FormulaError("FieldId {} is a invalid field id")
            return FieldRef(value)  # type: ignore[arg-type]
        if kind == "op" and value == "-":
            return Neg(self._expr(_UNARY_RBP))
        if kind == "lparen":
            inner = self._expr(0)
            if self._next()[0] != "rparen":
                raise FormulaError("missing closing parenthesis")
            return inner
        if kind == "ident":
            if self._peek()[0] != "lparen":
                raise FormulaError(f"unexpected identifier {value!r}")
            return self._call(value)  # type: ignore[arg-type]
        raise FormulaError("unexpected token in expression")

    def _call(self, name: str) -> Node:
        self._next()  # consume '('
        args: list[Node] = []
        if self._peek()[0] != "rparen":
            args.append(self._expr(0))
            while self._peek()[0] == "comma":
                self._next()
                args.append(self._expr(0))
        if self._next()[0] != "rparen":
            raise FormulaError("missing closing parenthesis in function call")
        return Call(name, tuple(args))


def parse(expression: str) -> Node:
    if expression is None or expression.strip() == "":
        raise FormulaError("empty expression")
    return _Parser(_tokenize(expression)).parse()


def reference_field_ids(node: Node) -> list[str]:
    seen: list[str] = []

    def walk(n: Node) -> None:
        if isinstance(n, FieldRef):
            if n.id not in seen:
                seen.append(n.id)
        elif isinstance(n, Neg):
            walk(n.operand)
        elif isinstance(n, Bin):
            walk(n.left)
            walk(n.right)
        elif isinstance(n, Call):
            for arg in n.args:
                walk(arg)

    walk(node)
    return seen
