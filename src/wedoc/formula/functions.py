"""Formula function library + argument type converter.

Ports the reference ``FUNCTIONS`` factory and ``TypedValueConverter``. Each
function exposes ``accept_types`` (ordered, the first entry is the coercion
target), ``accept_multiple`` and the ``return_type`` / ``eval`` pair the
evaluator drives. The common logical/text/numeric subset is defined inline;
the date/time, array, system and rarer numeric/text functions are defined
lower in the module and folded into ``_FUNCTION_LIST`` / ``FUNCTIONS`` so the
whole reference surface resolves.
"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from .parser import FormulaError
from .values import (
    BOOLEAN,
    DATETIME,
    NUMBER,
    STRING,
    FormulaBaseError,
    TypedValue,
    convert_value_to_string,
    js_str,
    to_number,
)


def _convert_unsupported(value: Any, in_type: str, accept: str) -> Any:
    if value is None:
        return None
    if accept == NUMBER:
        if in_type == STRING:
            n = to_number(value)
            return None if math.isnan(n) else n
        if in_type == BOOLEAN:
            return 1 if value else 0
        if in_type == DATETIME:
            return None
        return value
    if accept == BOOLEAN:
        return value if in_type == BOOLEAN else bool(value)
    if accept == STRING:
        return value if in_type in (STRING, DATETIME) else js_str(value)
    if accept == DATETIME:
        return value if in_type in (DATETIME, STRING) else None
    return value


# CONVERTER_MARKER


def _transform_multiple(tv: TypedValue, func: Func) -> TypedValue:
    if not tv.is_multiple or func.accept_multiple:
        return tv
    if isinstance(tv.value, list) and len(tv.value) > 1:
        raise FormulaError(f"function {func.name} is not accept array value: {tv.value}")
    trans = tv.value[0] if isinstance(tv.value, list) and tv.value else None
    return TypedValue(trans, tv.type)


def convert_typed_value(tv: TypedValue, func: Func) -> TypedValue:
    tv = _transform_multiple(tv, func)
    if tv.type in func.accept_types:
        return tv
    if not func.accept_types:
        raise FormulaError(f"function {func.name} has no acceptable value types")
    first = func.accept_types[0]
    if tv.is_multiple and isinstance(tv.value, list):
        converted: Any = [_convert_unsupported(v, tv.type, first) for v in tv.value]
    else:
        converted = _convert_unsupported(tv.value, tv.type, first)
    return TypedValue(None if converted is None else converted, first, tv.is_multiple)


class Func:
    name = ""
    accept_types: tuple[str, ...] = ()
    accept_multiple = False
    min_params = 0
    exact_params: int | None = None

    def validate(self, params: list[TypedValue]) -> None:
        if self.exact_params is not None and len(params) != self.exact_params:
            raise FormulaError(f"{self.name} needs {self.exact_params} param(s)")
        if len(params) < self.min_params:
            raise FormulaError(f"{self.name} needs at least {self.min_params} param(s)")

    def return_type(self, params: list[TypedValue]) -> tuple[str, bool]:
        self.validate(params)
        return (STRING, False)

    def eval(self, params: list[TypedValue], ctx: dict[str, Any]) -> Any:
        raise NotImplementedError


def _num(value: Any) -> float:
    return to_number(value)


# LOGICAL_MARKER

_ALL_TYPES = (STRING, DATETIME, NUMBER, BOOLEAN)


class If(Func):
    name = "IF"
    accept_types = _ALL_TYPES
    accept_multiple = True
    min_params = 3

    def return_type(self, params):
        self.validate(params)
        if params[1].is_blank:
            return (params[2].type, bool(params[2].is_multiple))
        if params[2].is_blank:
            return (params[1].type, bool(params[1].is_multiple))
        if params[1].type == params[2].type:
            return (params[1].type, bool(params[1].is_multiple and params[2].is_multiple))
        return (STRING, False)

    def eval(self, params, ctx):
        return params[1].value if params[0].value else params[2].value


class Switch(Func):
    name = "SWITCH"
    accept_types = _ALL_TYPES
    accept_multiple = True
    min_params = 2

    def return_type(self, params):
        self.validate(params)
        length = len(params)
        if length <= 2:
            return (params[1].type, bool(params[1].is_multiple))
        expected = params[2].type
        expected_multiple = bool(params[2].is_multiple)
        indices = list(range(2, length, 2))
        if length % 2 == 0:
            indices.append(length - 1)
        for i in indices:
            param = params[i]
            if not param.is_blank:
                if expected != param.type:
                    expected = STRING
                if expected_multiple != bool(param.is_multiple):
                    expected_multiple = False
        return (expected, expected_multiple)

    def eval(self, params, ctx):
        length = len(params)
        expression = params[0].value
        if length % 2 == 0:
            default = params[length - 1].value
            for i in range(1, length - 1, 2):
                if expression == params[i].value:
                    return params[i + 1].value
            return default
        for i in range(1, length, 2):
            if expression == params[i].value:
                return params[i + 1].value
        return None


class _BoolReduce(Func):
    accept_types = (BOOLEAN,)
    accept_multiple = True
    min_params = 1

    def return_type(self, params):
        self.validate(params)
        return (BOOLEAN, False)

    @staticmethod
    def _iter(params):
        for param in params:
            if param.is_multiple:
                if isinstance(param.value, list):
                    yield from (bool(v) for v in param.value)
            else:
                yield bool(param.value)


class And(_BoolReduce):
    name = "AND"

    def eval(self, params, ctx):
        return all(self._iter(params))


class Or(_BoolReduce):
    name = "OR"

    def eval(self, params, ctx):
        return any(self._iter(params))


class Xor(_BoolReduce):
    name = "XOR"

    def eval(self, params, ctx):
        return sum(1 for v in self._iter(params) if v) % 2 == 1


class Not(_BoolReduce):
    name = "NOT"
    exact_params = 1

    def eval(self, params, ctx):
        return not params[0].value


class Blank(Func):
    name = "BLANK"
    accept_types = ()

    def return_type(self, params):
        return (STRING, False)

    def eval(self, params, ctx):
        return None


class Error(Func):
    name = "ERROR"
    accept_types = (STRING,)
    accept_multiple = True

    def return_type(self, params):
        return (STRING, False)

    def eval(self, params, ctx):
        raise FormulaBaseErrorRaised(convert_value_to_string(params[0]) or "")


class IsError(Func):
    name = "IS_ERROR"
    accept_types = (STRING, NUMBER, BOOLEAN, DATETIME)
    accept_multiple = True
    exact_params = 1

    def return_type(self, params):
        self.validate(params)
        return (BOOLEAN, False)

    def eval(self, params, ctx):
        return isinstance(params[0].value, FormulaBaseError)


class FormulaBaseErrorRaised(FormulaError):
    """ERROR() raises this; the evaluator converts it to a FormulaBaseError."""


# TEXT_MARKER


def _arg(params: list[TypedValue], i: int) -> Any:
    return params[i].value if i < len(params) else None


class Concatenate(Func):
    name = "CONCATENATE"
    accept_types = (STRING,)
    accept_multiple = True
    min_params = 1

    def eval(self, params, ctx):
        result = ""
        for param in params:
            if param.is_multiple:
                if isinstance(param.value, list):
                    result += ", ".join("" if v is None else js_str(v) for v in param.value)
            else:
                result += param.value if param.value else ""
        return result


class Left(Func):
    name = "LEFT"
    accept_types = (STRING, NUMBER)
    accept_multiple = True
    min_params = 1

    def eval(self, params, ctx):
        value = convert_value_to_string(params[0])
        if value is None:
            return None
        count = int(to_number(_arg(params, 1) if len(params) > 1 else 1) or 1)
        return value[:count]


class Right(Func):
    name = "RIGHT"
    accept_types = (STRING, NUMBER)
    accept_multiple = True
    min_params = 1

    def eval(self, params, ctx):
        value = convert_value_to_string(params[0])
        if value is None:
            return None
        count = int(to_number(_arg(params, 1) if len(params) > 1 else 1) or 1)
        return value[max(0, len(value) - count):]


class Mid(Func):
    name = "MID"
    accept_types = (STRING, NUMBER)
    accept_multiple = True
    min_params = 3

    def eval(self, params, ctx):
        value = convert_value_to_string(params[0])
        if value is None:
            return None
        start = int(to_number(_arg(params, 1)))
        count = int(to_number(_arg(params, 2)))
        # 1-based start via JS substring semantics: clamp both bounds at 0 and
        # swap when reversed (MID("hello",0,2) -> substring(-1,1) -> "h").
        lo = max(start - 1, 0)
        hi = max(start - 1 + count, 0)
        if lo > hi:
            lo, hi = hi, lo
        return value[lo:hi]


class Len(Func):
    name = "LEN"
    accept_types = (STRING,)
    accept_multiple = True
    exact_params = 1

    def return_type(self, params):
        self.validate(params)
        return (NUMBER, False)

    def eval(self, params, ctx):
        value = convert_value_to_string(params[0])
        return None if value is None else len(value)


class _StringUnary(Func):
    accept_types = (STRING,)
    accept_multiple = True
    exact_params = 1


class Lower(_StringUnary):
    name = "LOWER"

    def eval(self, params, ctx):
        value = convert_value_to_string(params[0])
        return None if value is None else value.lower()


class Upper(_StringUnary):
    name = "UPPER"

    def eval(self, params, ctx):
        value = convert_value_to_string(params[0])
        return None if value is None else value.upper()


class Trim(_StringUnary):
    name = "TRIM"

    def eval(self, params, ctx):
        value = convert_value_to_string(params[0])
        return None if value is None else value.strip()


class Replace(Func):
    name = "REPLACE"
    accept_types = (STRING, NUMBER)
    accept_multiple = True
    min_params = 4

    def eval(self, params, ctx):
        target = convert_value_to_string(params[0])
        if target is None:
            return None
        start = int(to_number(_arg(params, 1)))
        count = int(to_number(_arg(params, 2)))
        replacement = js_str(_arg(params, 3)) if _arg(params, 3) is not None else ""
        if len(target) <= start:
            return target + replacement
        return target[: start - 1] + replacement + target[start + count - 1:]


class Substitute(Func):
    name = "SUBSTITUTE"
    accept_types = (STRING, NUMBER)
    accept_multiple = True
    min_params = 3

    def eval(self, params, ctx):
        target = convert_value_to_string(params[0])
        if target is None:
            return None
        old = js_str(_arg(params, 1)) if _arg(params, 1) is not None else ""
        new = js_str(_arg(params, 2)) if _arg(params, 2) is not None else ""
        if old == "":
            return target
        index = int(to_number(_arg(params, 3))) - 1 if len(params) > 3 else -1
        parts = target.split(old)
        if index > len(parts) - 2:
            return target
        if index > 0:
            substituter = new.join([parts[index], parts[index + 1]])
            parts[index:index + 2] = [substituter]
            return old.join(parts)
        return new.join(parts)


class T(Func):
    name = "T"
    accept_types = (STRING, NUMBER, BOOLEAN, DATETIME)
    accept_multiple = True
    exact_params = 1

    def eval(self, params, ctx):
        value = params[0].value
        if params[0].is_multiple and isinstance(value, list):
            if any(v is not None and not isinstance(v, str) for v in value):
                return None
            return ", ".join(v for v in value if v)
        return value if isinstance(value, str) else None


class Search(Func):
    name = "SEARCH"
    accept_types = (STRING, NUMBER)
    accept_multiple = True
    min_params = 1

    def return_type(self, params):
        self.validate(params)
        return (NUMBER, False)

    def eval(self, params, ctx):
        find = _arg(params, 0)
        target = convert_value_to_string(params[1]) if len(params) > 1 else None
        if find is None or target is None:
            return None
        start = _arg(params, 2)
        begin = int(start) - 1 if isinstance(start, (int, float)) and start > 0 else 0
        pos = str(target).find(str(find), begin) + 1
        return None if pos == 0 else pos


class Rept(Func):
    name = "REPT"
    accept_types = (STRING, NUMBER)
    accept_multiple = True
    min_params = 2

    def eval(self, params, ctx):
        value = convert_value_to_string(params[0])
        if value is None:
            return None
        count = int(to_number(_arg(params, 1)) or 0)
        if count <= 0:
            return None
        return str(value) * count


# NUMERIC_MARKER


def _js_round(x: float) -> float:
    return math.floor(x + 0.5)


def _to_int32(x: float) -> int:
    try:
        n = int(x)
    except (ValueError, OverflowError):
        return 0
    n &= 0xFFFFFFFF
    return n - 0x100000000 if n >= 0x80000000 else n


def _parse_float(text: str) -> float:
    match = re.match(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?", text)
    if not match:
        return math.nan
    try:
        return float(match.group(0))
    except ValueError:
        return math.nan


class _NumericReduce(Func):
    accept_types = (NUMBER,)
    accept_multiple = True
    min_params = 1

    def return_type(self, params):
        self.validate(params)
        return (NUMBER, False)

    @staticmethod
    def _iter(params):
        for param in params:
            if param.is_multiple:
                if isinstance(param.value, list):
                    yield from param.value
            else:
                yield param.value


class Sum(_NumericReduce):
    name = "SUM"

    def eval(self, params, ctx):
        return sum((v or 0) for v in self._iter(params))


class Average(_NumericReduce):
    name = "AVERAGE"

    def eval(self, params, ctx):
        total = 0.0
        count = 0
        for param in params:
            if param.is_multiple:
                if isinstance(param.value, list):
                    count += len(param.value)
                    total += sum((v or 0) for v in param.value)
            else:
                count += 1
                total += param.value or 0
        return None if count == 0 else total / count


class _MinMax(Func):
    accept_types = (NUMBER, DATETIME)
    accept_multiple = True
    min_params = 1
    _is_max = True

    def return_type(self, params):
        self.validate(params)
        return (params[0].type if params else NUMBER, False)

    def eval(self, params, ctx):
        is_dt = params[0].type == DATETIME
        candidates: list[Any] = []
        for param in params:
            if param.is_multiple and isinstance(param.value, list):
                values = param.value
            else:
                values = [param.value]
            candidates.extend(v for v in values if v is not None)
        if not candidates:
            return None
        pick = max if self._is_max else min
        if is_dt:
            pairs = [(_date_ms(v), v) for v in candidates]
            pairs = [p for p in pairs if p[0] is not None]
            if not pairs:
                return None
            return _ms_iso(pick(pairs, key=lambda p: p[0])[0])
        nums = [to_number(v) for v in candidates]
        nums = [n for n in nums if not math.isnan(n)]
        return pick(nums) if nums else None


class Max(_MinMax):
    name = "MAX"
    _is_max = True


class Min(_MinMax):
    name = "MIN"
    _is_max = False


class _NumericUnary(Func):
    accept_types = (NUMBER,)
    accept_multiple = False

    def return_type(self, params):
        self.validate(params)
        return (NUMBER, False)


class Round(_NumericUnary):
    name = "ROUND"
    min_params = 1

    def eval(self, params, ctx):
        value = params[0].value
        if value is None:
            return None
        precision = math.floor(params[1].value) if len(params) > 1 and params[1].value else 0
        offset = 10 ** precision
        return _js_round(value * offset) / offset


class RoundUp(_NumericUnary):
    name = "ROUNDUP"
    min_params = 1

    def eval(self, params, ctx):
        value = params[0].value
        if value is None:
            return None
        value = to_number(value)
        precision = math.floor(params[1].value) if len(params) > 1 and params[1].value else 0
        offset = 10 ** precision
        fn = math.ceil if value > 0 else math.floor
        return fn(value * offset) / offset


class RoundDown(_NumericUnary):
    name = "ROUNDDOWN"
    min_params = 1

    def eval(self, params, ctx):
        value = params[0].value
        if value is None:
            return None
        value = to_number(value)
        precision = math.floor(params[1].value) if len(params) > 1 and params[1].value else 0
        offset = 10 ** precision
        fn = math.floor if value > 0 else math.ceil
        return fn(value * offset) / offset


class Ceiling(_NumericUnary):
    name = "CEILING"
    min_params = 1

    def eval(self, params, ctx):
        value = params[0].value
        if value is None:
            return None
        places = (len(params) > 1 and params[1].value) or 0
        mult = 10 ** places
        return math.ceil(value * mult) / mult


class Floor(_NumericUnary):
    name = "FLOOR"
    min_params = 1

    def eval(self, params, ctx):
        value = params[0].value
        if value is None:
            return None
        places = (len(params) > 1 and params[1].value) or 0
        mult = 10 ** places
        return math.floor(value * mult) / mult


class Int(_NumericUnary):
    name = "INT"
    exact_params = 1

    def eval(self, params, ctx):
        value = params[0].value
        return None if value is None else math.floor(value)


class Abs(_NumericUnary):
    name = "ABS"
    exact_params = 1

    def eval(self, params, ctx):
        value = params[0].value
        return None if value is None else abs(value)


class Sqrt(_NumericUnary):
    name = "SQRT"
    exact_params = 1

    def eval(self, params, ctx):
        value = params[0].value
        if value is None:
            return None
        return math.sqrt(value) if value >= 0 else math.nan


class Power(_NumericUnary):
    name = "POWER"
    min_params = 2

    def eval(self, params, ctx):
        value = params[0].value
        if value is None:
            return None
        exponent = (len(params) > 1 and params[1].value) or 1
        try:
            return math.pow(value, exponent)
        except (ValueError, OverflowError):
            return math.nan


class Mod(_NumericUnary):
    name = "MOD"
    min_params = 2

    def eval(self, params, ctx):
        value = params[0].value
        if value is None:
            return None
        divisor = (len(params) > 1 and params[1].value) or 1
        mod = math.fmod(value, divisor)
        return -mod if (_to_int32(value) ^ _to_int32(divisor)) < 0 else mod


class Value(_NumericUnary):
    name = "VALUE"
    accept_types = (STRING,)
    exact_params = 1

    def eval(self, params, ctx):
        value = params[0].value
        if value is None:
            return None
        cleaned = re.sub(r"[^\d.+-]", "", js_str(value))
        cleaned = re.sub(r"([+\-.])+", r"\1", cleaned)
        return _parse_float(cleaned)


# REGISTRY_MARKER


def _date_ms(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        from datetime import datetime

        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.timestamp() * 1000
    except ValueError:
        return None


def _ms_iso(ms: float) -> str:
    from datetime import UTC, datetime

    parsed = datetime.fromtimestamp(ms / 1000, tz=UTC)
    return parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z")


_FUNCTION_LIST: list[Func] = [
    If(), Switch(), And(), Or(), Xor(), Not(), Blank(), Error(), IsError(),
    Concatenate(), Left(), Right(), Mid(), Len(), Lower(), Upper(), Trim(),
    Replace(), Substitute(), T(), Search(), Rept(),
    Sum(), Average(), Max(), Min(), Round(), RoundUp(), RoundDown(), Ceiling(),
    Floor(), Int(), Abs(), Sqrt(), Power(), Mod(), Value(),
]

FUNCTIONS: dict[str, Func] = {func.name: func for func in _FUNCTION_LIST}

# Non-canonical tokens accepted in expressions (Airtable-style + reference
# aliases) mapped to the registered canonical name.
_ALIASES = {
    "ISERROR": "IS_ERROR",
    "ISERR": "IS_ERROR",
    "ARRAYJOIN": "ARRAY_JOIN",
}


def resolve_function(token: str) -> Func | None:
    upper = token.upper()
    return FUNCTIONS.get(_ALIASES.get(upper, upper))



# EXTENDED_MARKER: date/time, array, system and rarer numeric/text functions.
# Semantics mirror the reference engine's eval() exactly, including its
# timezone-aware date handling (ported from the reference's dayjs usage) and
# its JS coercion quirks.


def _js_log(x: float) -> float:
    x = float(x)
    if math.isnan(x) or x < 0:
        return math.nan
    if x == 0:
        return -math.inf
    return math.log(x)


def _js_div(a: float, b: float) -> float:
    if b == 0:
        if a == 0 or math.isnan(a):
            return math.nan
        return math.inf if a > 0 else -math.inf
    return a / b


class Even(_NumericUnary):
    name = "EVEN"
    exact_params = 1

    def eval(self, params, ctx):
        value = params[0].value
        if value is None:
            return None
        # oracle rounds toward -inf then bumps up to the next even integer.
        rounded = math.floor(value)
        return rounded if rounded % 2 == 0 else rounded + 1


class Odd(_NumericUnary):
    name = "ODD"
    exact_params = 1

    def eval(self, params, ctx):
        value = params[0].value
        if value is None:
            return None
        # oracle rounds toward -inf then bumps up to the next odd integer.
        rounded = math.floor(value)
        return rounded if rounded % 2 != 0 else rounded + 1


class Exp(_NumericUnary):
    name = "EXP"
    exact_params = 1

    def eval(self, params, ctx):
        value = params[0].value
        if value is None:
            return None
        try:
            return math.exp(value)
        except OverflowError:
            return math.inf


class Log(_NumericUnary):
    name = "LOG"
    min_params = 1

    def eval(self, params, ctx):
        value = params[0].value
        if value is None:
            return None
        base = _arg(params, 1)
        # oracle defaults to the natural log; an explicit base gives log_base.
        if base:
            return _js_div(_js_log(value), _js_log(base))
        return _js_log(value)


class Find(Func):
    name = "FIND"
    accept_types = (STRING, NUMBER)
    accept_multiple = True
    min_params = 1

    def return_type(self, params):
        self.validate(params)
        return (NUMBER, False)

    def eval(self, params, ctx):
        find = _arg(params, 0)
        target = convert_value_to_string(params[1]) if len(params) > 1 else None
        if find is None or target is None:
            return None
        start = _arg(params, 2)
        usable = isinstance(start, (int, float)) and not isinstance(start, bool) and start > 0
        begin = int(start) - 1 if usable else 0
        return target.find(js_str(find), begin) + 1


class EncodeUrlComponent(Func):
    name = "ENCODE_URL_COMPONENT"
    accept_types = (STRING,)
    accept_multiple = True
    exact_params = 1

    def eval(self, params, ctx):
        value = convert_value_to_string(params[0])
        if value is None:
            return None
        return quote(value, safe="!*'()")


def _js_regexp_replace(pattern: str, replacement: str, text: str) -> str:
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        raise FormulaBaseErrorRaised(f"invalid regexp: {pattern}") from exc
    # oracle inserts the replacement verbatim (no $1/$& backreference expansion).
    return regex.sub(lambda _m: replacement, text)


class RegExpReplace(Func):
    name = "REGEXP_REPLACE"
    accept_types = (STRING,)
    accept_multiple = True
    min_params = 3

    def eval(self, params, ctx):
        text = convert_value_to_string(params[0])
        if text is None:
            return None
        pattern = js_str(_arg(params, 1)) if _arg(params, 1) else ""
        replacement = js_str(_arg(params, 2)) if _arg(params, 2) else ""
        return _js_regexp_replace(pattern, replacement, text)


class TextBefore(Func):
    name = "TEXTBEFORE"
    accept_types = (STRING, NUMBER)
    accept_multiple = True
    min_params = 2

    def eval(self, params, ctx):
        target = convert_value_to_string(params[0])
        delimiter = convert_value_to_string(params[1])
        if target is None or delimiter is None:
            return None
        # oracle returns the text before the first delimiter, ignoring the
        # instance/match-mode arguments the reference source documents.
        if delimiter == "":
            return ""
        idx = target.find(delimiter)
        return target[:idx] if idx != -1 else None


class TextSplit(Func):
    name = "TEXTSPLIT"
    accept_types = (STRING, NUMBER)
    accept_multiple = True
    min_params = 2

    def return_type(self, params):
        self.validate(params)
        return (STRING, True)

    def eval(self, params, ctx):
        target = convert_value_to_string(params[0])
        delimiter = convert_value_to_string(params[1])
        if target is None or delimiter is None:
            return None
        # oracle splits on the delimiter and keeps every part; the ignore-empty
        # / match-mode arguments in the reference source are not honored.
        return list(target) if delimiter == "" else target.split(delimiter)


_ISO_DATE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?(?:Z|[+-]\d{2}:\d{2})$"
)

# reference dateAdd interval-unit aliases (trimmed + case-insensitive); unknown -> null.
_DATEADD_UNITS: dict[str, tuple[str, int]] = {
    "millisecond": ("ms", 1), "milliseconds": ("ms", 1), "ms": ("ms", 1),
    "second": ("s", 1), "seconds": ("s", 1), "s": ("s", 1), "sec": ("s", 1), "secs": ("s", 1),
    "minute": ("m", 1), "minutes": ("m", 1), "min": ("m", 1), "mins": ("m", 1),
    "hour": ("h", 1), "hours": ("h", 1), "h": ("h", 1), "hr": ("h", 1), "hrs": ("h", 1),
    "day": ("d", 1), "days": ("d", 1),
    "week": ("w", 1), "weeks": ("w", 1),
    "month": ("M", 1), "months": ("M", 1),
    "quarter": ("M", 3), "quarters": ("M", 3),
    "year": ("y", 1), "years": ("y", 1),
}

# reference datetimeDiff unit aliases (trimmed + case-insensitive); unknown -> null.
# distinct from dateAdd: bare "m" resolves to minute here, and single-char
# d/w/y/q are not accepted (they collapse to null).
_DIFF_UNITS: dict[str, str] = {
    "millisecond": "millisecond", "milliseconds": "millisecond", "ms": "millisecond",
    "second": "second", "seconds": "second", "s": "second", "sec": "second", "secs": "second",
    "minute": "minute", "minutes": "minute", "m": "minute", "min": "minute", "mins": "minute",
    "hour": "hour", "hours": "hour", "h": "hour", "hr": "hour", "hrs": "hour",
    "day": "day", "days": "day",
    "week": "week", "weeks": "week",
    "month": "month", "months": "month",
    "quarter": "quarter", "quarters": "quarter",
    "year": "year", "years": "year",
}

# reference isSame truncate-unit aliases (trimmed + case-insensitive); unknown -> null.
# narrower than diff: quarter and millisecond are NOT accepted, and bare single-char
# codes other than h/s are rejected.
_ISSAME_UNITS: dict[str, str] = {
    "second": "second", "seconds": "second", "s": "second", "sec": "second", "secs": "second",
    "minute": "minute", "minutes": "minute", "min": "minute", "mins": "minute",
    "hour": "hour", "hours": "hour", "h": "hour", "hr": "hour", "hrs": "hour",
    "day": "day", "days": "day",
    "week": "week", "weeks": "week",
    "month": "month", "months": "month",
    "year": "year", "years": "year",
}

_LOCAL_PATTERNS = (
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
)


def _zone(tz: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def _parse_local(value: str, zone: ZoneInfo) -> datetime | None:
    text = value.strip().replace("/", "-").replace("T", " ")
    for pattern in _LOCAL_PATTERNS:
        try:
            return datetime.strptime(text, pattern).replace(tzinfo=zone)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=zone) if parsed.tzinfo is None else parsed.astimezone(zone)


def _get_dayjs(value, tz, custom_format=None):
    if value is None:
        return None
    zone = _zone(tz)
    if not isinstance(value, str):
        raise FormulaBaseErrorRaised("invalid date value")
    if custom_format:
        parsed = _parse_with_format(value, custom_format, zone)
    elif _ISO_DATE_RE.match(value):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(zone)
        except ValueError as exc:
            raise FormulaBaseErrorRaised("invalid date value") from exc
    else:
        parsed = _parse_local(value, zone)
    if parsed is None:
        raise FormulaBaseErrorRaised("invalid date value")
    return parsed


def _dow(dt: datetime) -> int:
    """dayjs .day(): 0 = Sunday .. 6 = Saturday."""
    return dt.isoweekday() % 7


def _ms(dt: datetime) -> float:
    return dt.timestamp() * 1000


def _to_iso_z(dt: datetime) -> str:
    u = dt.astimezone(UTC)
    return u.strftime("%Y-%m-%dT%H:%M:%S.") + f"{u.microsecond // 1000:03d}Z"


def _start_of_week(dt: datetime) -> datetime:
    midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight - timedelta(days=_dow(dt))


def _week_of_year(dt: datetime) -> int:
    if dt.month == 12 and dt.day > 25:
        next_year_start = dt.replace(
            year=dt.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0
        )
        end_of_week = _start_of_week(dt) + timedelta(days=7) - timedelta(milliseconds=1)
        if _ms(next_year_start) < _ms(end_of_week):
            return 1
    year_start = dt.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    year_start_week = _start_of_week(year_start) - timedelta(milliseconds=1)
    diff_weeks = (_ms(dt) - _ms(year_start_week)) / 604800000
    return math.ceil(diff_weeks)


def _days_in_month(year: int, month: int) -> int:
    nxt = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    return (nxt - datetime(year, month, 1)).days


def _add_months(dt: datetime, months: int) -> datetime:
    total = dt.month - 1 + months
    year = dt.year + total // 12
    month = total % 12 + 1
    day = min(dt.day, _days_in_month(year, month))
    return dt.replace(year=year, month=month, day=day)


def _add_days(dt: datetime, days: int) -> datetime:
    naive = dt.replace(tzinfo=None) + timedelta(days=days)
    return naive.replace(tzinfo=dt.tzinfo)


def _pg_cast_int(value: float) -> int:
    # Postgres CAST(numeric AS integer): round half away from zero.
    return math.floor(value + 0.5) if value >= 0 else math.ceil(value - 0.5)


def _b10000_weight_first(value: Decimal) -> tuple[int, int]:
    # base-NBASE(10000) weight and leading 4-digit group of |value| (value != 0),
    # as used by Postgres numeric division scale selection.
    text = format(abs(value), "f")
    int_part = text.split(".")[0].lstrip("0")
    if int_part:
        weight = (len(int_part) - 1) // 4
        return weight, int(int_part[: len(int_part) - weight * 4])
    frac = text.split(".")[1] if "." in text else ""
    weight = -1
    for i in range(0, len(frac), 4):
        group = int(frac[i : i + 4].ljust(4, "0"))
        if group:
            return weight, group
        weight -= 1
    return 0, 0


def _pg_numeric_div(dividend: Decimal, divisor: Decimal) -> float:
    # replicate Postgres numeric `/`: select a result scale giving >=16 significant
    # digits (select_div_scale), round half away from zero, then cast to float8.
    d1 = max(-dividend.as_tuple().exponent, 0)
    d2 = max(-divisor.as_tuple().exponent, 0)
    if dividend == 0:
        return 0.0
    w1, f1 = _b10000_weight_first(dividend)
    w2, f2 = _b10000_weight_first(divisor)
    qweight = w1 - w2 - (1 if f1 <= f2 else 0)
    rscale = max(16 - qweight * 4, d1, d2, 0)
    with localcontext() as ctx:
        ctx.prec = 60
        quotient = (dividend / divisor).quantize(
            Decimal(1).scaleb(-rscale), rounding=ROUND_HALF_UP
        )
    return float(quotient)


def _sql_month_diff(start: datetime, end: datetime) -> int:
    # reference buildMonthDiff: integer calendar-month delta with end-of-month guard.
    base = (start.year - end.year) * 12 + (start.month - end.month)
    start_last = _days_in_month(start.year, start.month)
    end_last = _days_in_month(end.year, end.month)
    adjust_down = 1 if (base > 0 and start.day < end.day and start.day < start_last) else 0
    adjust_up = 1 if (base < 0 and start.day > end.day and end.day < end_last) else 0
    return base - adjust_down + adjust_up


def _pg_age_ym(t1: datetime, t2: datetime) -> tuple[int, int]:
    # (years, months) components of Postgres AGE(t1, t2): field-wise subtraction with
    # borrowing (a borrowed day adds the day-count of t2's month); antisymmetric.
    if _ms(t1) < _ms(t2):
        year, mon = _pg_age_ym(t2, t1)
        return -year, -mon
    usec = t1.microsecond - t2.microsecond
    sec = t1.second - t2.second
    minute = t1.minute - t2.minute
    hour = t1.hour - t2.hour
    day = t1.day - t2.day
    mon = t1.month - t2.month
    year = t1.year - t2.year
    if usec < 0:
        sec -= 1
    if sec < 0:
        minute -= 1
    if minute < 0:
        hour -= 1
    if hour < 0:
        day -= 1
    while day < 0:
        day += _days_in_month(t2.year, t2.month)
        mon -= 1
    while mon < 0:
        mon += 12
        year -= 1
    return year, mon


def _dayjs_add(dt: datetime, count: float, unit: str) -> datetime:
    if unit == "y":
        year = dt.year + int(count)
        day = min(dt.day, _days_in_month(year, dt.month))
        return dt.replace(year=year, day=day)
    if unit == "M":
        return _add_months(dt, int(count))
    if unit == "w":
        return _add_days(dt, round(count) * 7)
    if unit == "d":
        return _add_days(dt, round(count))
    step = {"h": 3600000, "m": 60000, "s": 1000, "ms": 1}[unit]
    return dt + timedelta(milliseconds=count * step)


def _start_of(dt: datetime, unit: str) -> datetime:
    if unit == "year":
        return dt.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    if unit == "month":
        return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if unit == "week":
        return _start_of_week(dt)
    if unit in ("day", "date"):
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)
    if unit == "hour":
        return dt.replace(minute=0, second=0, microsecond=0)
    if unit == "minute":
        return dt.replace(second=0, microsecond=0)
    if unit == "second":
        return dt.replace(microsecond=0)
    return dt


def _end_of(dt: datetime, unit: str) -> datetime:
    start = _start_of(dt, unit)
    if unit == "year":
        nxt = start.replace(year=start.year + 1)
    elif unit == "month":
        nxt = _add_months(start, 1)
    elif unit == "week":
        nxt = _add_days(start, 7)
    elif unit in ("day", "date"):
        nxt = _add_days(start, 1)
    elif unit == "hour":
        nxt = start + timedelta(hours=1)
    elif unit == "minute":
        nxt = start + timedelta(minutes=1)
    elif unit == "second":
        nxt = start + timedelta(seconds=1)
    else:
        return dt
    return nxt - timedelta(milliseconds=1)


def _same_day(a: datetime, b: datetime) -> bool:
    return _ms(_start_of(a, "day")) == _ms(_start_of(b, "day"))


def _date_trunc_same(dt: datetime, unit: str) -> datetime:
    # reference isSame uses Postgres DATE_TRUNC; its week starts on Monday (ISO),
    # unlike the Sunday-based week used elsewhere.
    if unit == "week":
        midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        return midnight - timedelta(days=dt.weekday())
    return _start_of(dt, unit)


_MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
_MONTH_ABBR = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)
_DAY_NAMES = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")
_DAY_ABBR = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")

_LOCALIZED_FORMAT_MAP = {
    "LTS": "h:mm:ss A",
    "LT": "h:mm A",
    "LLLL": "dddd, MMMM D, YYYY h:mm A",
    "LLL": "MMMM D, YYYY h:mm A",
    "LL": "MMMM D, YYYY",
    "L": "MM/DD/YYYY",
    "llll": "ddd, MMM D, YYYY h:mm A",
    "lll": "MMM D, YYYY h:mm A",
    "ll": "MMM D, YYYY",
    "l": "M/D/YYYY",
}
_LOCALIZED_TOKENS = sorted(_LOCALIZED_FORMAT_MAP, key=len, reverse=True)

# reference supported format tokens, matched longest-first; single-char tokens only
# match when not adjacent to a letter (mirrors shouldMatchSingleCharToken).
_SUPPORTED_FMT_TOKENS = sorted(
    (
        "HH24", "HH12", "MI", "MS", "SS", "Month", "MONTH", "month",
        "Day", "DAY", "day", "YYYY", "MMMM", "dddd", "ddd", "dd", "d",
        "MMM", "YY", "MM", "M", "DD", "D", "HH", "H", "hh", "h",
        "mm", "m", "ss", "s", "SSS", "ZZ", "Z", "A", "a",
    ),
    key=len,
    reverse=True,
)


def _is_fmt_alpha(ch: str) -> bool:
    return ch.isascii() and ch.isalpha()


def _should_match_single(literal: str, i: int) -> bool:
    prev = literal[i - 1] if i > 0 else ""
    nxt = literal[i + 1] if i + 1 < len(literal) else ""
    return not _is_fmt_alpha(prev) and not _is_fmt_alpha(nxt)


def _expand_localized(literal: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(literal):
        tok = next((t for t in _LOCALIZED_TOKENS if literal.startswith(t, i)), None)
        if tok is not None and (len(tok) > 1 or _should_match_single(literal, i)):
            out.append(_LOCALIZED_FORMAT_MAP[tok])
            i += len(tok)
        else:
            out.append(literal[i])
            i += 1
    return "".join(out)


def _format_offset(dt: datetime, compact: bool) -> str:
    off = dt.utcoffset() or timedelta(0)
    total = int(off.total_seconds() // 60)
    sign = "+" if total >= 0 else "-"
    hh, mm = divmod(abs(total), 60)
    return f"{sign}{hh:02d}{mm:02d}" if compact else f"{sign}{hh:02d}:{mm:02d}"


def _format_dayjs(dt: datetime, fmt: str) -> str:
    dow = _dow(dt)
    h12 = dt.hour % 12 or 12
    month_name = _MONTH_NAMES[dt.month - 1]
    day_name = _DAY_NAMES[dow]
    ampm = "AM" if dt.hour < 12 else "PM"
    tokens = {
        "HH24": f"{dt.hour:02d}",
        "HH12": f"{h12:02d}",
        "MI": f"{dt.minute:02d}",
        "MS": f"{dt.microsecond // 1000:03d}",
        "SS": f"{dt.second:02d}",
        "Month": month_name,
        "MONTH": month_name.upper(),
        "month": month_name.lower(),
        "Day": day_name,
        "DAY": day_name.upper(),
        "day": day_name.lower(),
        "YYYY": f"{dt.year:04d}",
        "MMMM": month_name,
        "dddd": day_name,
        "ddd": _DAY_ABBR[dow],
        "dd": _DAY_ABBR[dow][:2],
        "d": str(dow),
        "MMM": _MONTH_ABBR[dt.month - 1],
        "YY": f"{dt.year % 100:02d}",
        "MM": f"{dt.month:02d}",
        "M": str(dt.month),
        "DD": f"{dt.day:02d}",
        "D": str(dt.day),
        "HH": f"{dt.hour:02d}",
        "H": str(dt.hour),
        "hh": f"{h12:02d}",
        "h": str(h12),
        "mm": f"{dt.minute:02d}",
        "m": str(dt.minute),
        "ss": f"{dt.second:02d}",
        "s": str(dt.second),
        "SSS": f"{dt.microsecond // 1000:03d}",
        "ZZ": _format_offset(dt, True),
        "Z": _format_offset(dt, False),
        "A": ampm,
        "a": ampm.lower(),
    }
    literal = _expand_localized(fmt)
    out: list[str] = []
    i = 0
    while i < len(literal):
        tok = next((t for t in _SUPPORTED_FMT_TOKENS if literal.startswith(t, i)), None)
        if tok is not None and (len(tok) > 1 or _should_match_single(literal, i)):
            out.append(tokens[tok])
            i += len(tok)
        else:
            out.append(literal[i])
            i += 1
    return "".join(out)


_PARSE_TOKEN_RE = re.compile(r"YYYY|YY|MM|M|DD|D|HH|H|hh|h|mm|m|ss|s|SSS|SS|S|A|a")
_PARSE_TOKEN_PATTERNS = {
    "YYYY": r"\d{4}", "YY": r"\d{2}",
    "MM": r"\d{1,2}", "M": r"\d{1,2}",
    "DD": r"\d{1,2}", "D": r"\d{1,2}",
    "HH": r"\d{1,2}", "H": r"\d{1,2}",
    "hh": r"\d{1,2}", "h": r"\d{1,2}",
    "mm": r"\d{1,2}", "m": r"\d{1,2}",
    "ss": r"\d{1,2}", "s": r"\d{1,2}",
    "SSS": r"\d{3}", "SS": r"\d{2}", "S": r"\d{1,3}",
    "A": r"[AaPp][Mm]", "a": r"[AaPp][Mm]",
}


def _assign_component(comps: dict[str, int], tok: str, raw: str) -> None:
    if tok == "YYYY":
        comps["year"] = int(raw)
    elif tok == "YY":
        comps["year"] = 2000 + int(raw)
    elif tok in ("MM", "M"):
        comps["month"] = int(raw)
    elif tok in ("DD", "D"):
        comps["day"] = int(raw)
    elif tok in ("HH", "H", "hh", "h"):
        comps["hour"] = int(raw)
    elif tok in ("mm", "m"):
        comps["minute"] = int(raw)
    elif tok in ("ss", "s"):
        comps["second"] = int(raw)
    elif tok in ("SSS", "SS", "S"):
        comps["micro"] = int(raw.ljust(3, "0")[:3]) * 1000


def _parse_with_format(value: str, fmt: str, zone: ZoneInfo) -> datetime | None:
    groups: list[str] = []
    parts: list[str] = []
    pos = 0
    for match in _PARSE_TOKEN_RE.finditer(fmt):
        if match.start() > pos:
            parts.append(re.escape(fmt[pos:match.start()]))
        tok = match.group(0)
        groups.append(tok)
        parts.append("(" + _PARSE_TOKEN_PATTERNS[tok] + ")")
        pos = match.end()
    if pos < len(fmt):
        parts.append(re.escape(fmt[pos:]))
    try:
        regex = re.compile("^" + "".join(parts) + "$")
    except re.error:
        return None
    matched = regex.match(value.strip())
    if matched is None:
        return None
    comps = {"year": 1970, "month": 1, "day": 1, "hour": 0, "minute": 0, "second": 0, "micro": 0}
    meridiem = None
    for tok, raw in zip(groups, matched.groups(), strict=False):
        _assign_component(comps, tok, raw)
        if tok in ("A", "a"):
            meridiem = raw.lower()
    if meridiem == "pm" and comps["hour"] < 12:
        comps["hour"] += 12
    elif meridiem == "am" and comps["hour"] == 12:
        comps["hour"] = 0
    try:
        return datetime(
            comps["year"], comps["month"], comps["day"], comps["hour"],
            comps["minute"], comps["second"], comps["micro"], tzinfo=zone,
        )
    except ValueError:
        return None


class _DateUnaryNum(Func):
    accept_types = (DATETIME,)
    accept_multiple = False
    exact_params = 1

    def return_type(self, params):
        self.validate(params)
        return (NUMBER, False)


class _DateUnaryStr(Func):
    accept_types = (DATETIME,)
    accept_multiple = False
    exact_params = 1

    def return_type(self, params):
        self.validate(params)
        return (STRING, False)


class Year(_DateUnaryNum):
    name = "YEAR"

    def eval(self, params, ctx):
        dt = _get_dayjs(params[0].value, ctx["timeZone"])
        return None if dt is None else dt.year


class Month(_DateUnaryNum):
    name = "MONTH"

    def eval(self, params, ctx):
        dt = _get_dayjs(params[0].value, ctx["timeZone"])
        return None if dt is None else dt.month


class Day(_DateUnaryNum):
    name = "DAY"

    def eval(self, params, ctx):
        dt = _get_dayjs(params[0].value, ctx["timeZone"])
        return None if dt is None else dt.day


class WeekNum(_DateUnaryNum):
    name = "WEEKNUM"

    def eval(self, params, ctx):
        dt = _get_dayjs(params[0].value, ctx["timeZone"])
        return None if dt is None else _week_of_year(dt)


class Hour(_DateUnaryNum):
    name = "HOUR"

    def eval(self, params, ctx):
        dt = _get_dayjs(params[0].value, ctx["timeZone"])
        return None if dt is None else dt.hour


class Minute(_DateUnaryNum):
    name = "MINUTE"

    def eval(self, params, ctx):
        dt = _get_dayjs(params[0].value, ctx["timeZone"])
        return None if dt is None else dt.minute


class Second(_DateUnaryNum):
    name = "SECOND"

    def eval(self, params, ctx):
        dt = _get_dayjs(params[0].value, ctx["timeZone"])
        return None if dt is None else dt.second


class Datestr(_DateUnaryStr):
    name = "DATESTR"

    def eval(self, params, ctx):
        dt = _get_dayjs(params[0].value, ctx["timeZone"])
        return None if dt is None else dt.strftime("%Y-%m-%d")


class Timestr(_DateUnaryStr):
    name = "TIMESTR"

    def eval(self, params, ctx):
        dt = _get_dayjs(params[0].value, ctx["timeZone"])
        return None if dt is None else dt.strftime("%H:%M:%S")


class Weekday(Func):
    name = "WEEKDAY"
    accept_types = (DATETIME, STRING)
    accept_multiple = False
    min_params = 1

    def return_type(self, params):
        self.validate(params)
        return (NUMBER, False)

    def eval(self, params, ctx):
        dt = _get_dayjs(params[0].value, ctx["timeZone"])
        if dt is None:
            return None
        start = _arg(params, 1)
        weekday = _dow(dt)
        if str("sunday" if start is None else start).lower() == "monday":
            return 6 if weekday == 0 else weekday - 1
        return weekday


class DateAdd(Func):
    name = "DATE_ADD"
    accept_types = (DATETIME, STRING, NUMBER)
    accept_multiple = False
    min_params = 3

    def return_type(self, params):
        self.validate(params)
        return (DATETIME, False)

    def eval(self, params, ctx):
        dt = _get_dayjs(params[0].value, ctx["timeZone"])
        if dt is None:
            return None
        count = to_number(_arg(params, 1)) if _arg(params, 1) is not None else 0
        unit_raw = js_str(_arg(params, 2)) if _arg(params, 2) is not None else ""
        resolved = _DATEADD_UNITS.get(unit_raw.strip().lower())
        if resolved is None:
            return None
        code, factor = resolved
        return _to_iso_z(_dayjs_add(dt, count * factor, code))


class DatetimeDiff(Func):
    name = "DATETIME_DIFF"
    accept_types = (DATETIME, STRING, BOOLEAN)
    accept_multiple = False
    min_params = 2

    def return_type(self, params):
        self.validate(params)
        return (NUMBER, False)

    def eval(self, params, ctx):
        start = _get_dayjs(params[0].value, ctx["timeZone"])
        end = _get_dayjs(params[1].value, ctx["timeZone"])
        if start is None or end is None:
            return None
        raw = _arg(params, 2)
        unit = _DIFF_UNITS.get((js_str(raw) if raw is not None else "second").strip().lower())
        if unit is None:
            return None
        # reference computes EXTRACT(EPOCH FROM start - end) as numeric seconds (scale 6)
        # then scales via Postgres numeric division; mirror that exactly (numeric-then-
        # float8) so the low-order float digits match. month/quarter/year use an integer
        # calendar diff; the optional isFloat arg is ignored.
        epoch = (Decimal(round(_ms(start) - _ms(end))) / 1000).quantize(Decimal("0.000001"))
        if unit == "millisecond":
            return float(epoch * 1000)
        if unit == "second":
            return float(epoch)
        if unit == "minute":
            return _pg_numeric_div(epoch, Decimal(60))
        if unit == "hour":
            return _pg_numeric_div(epoch, Decimal(3600))
        if unit == "day":
            # the reference day-diff collapses sub-second intervals to 0 (whole-second
            # part zero) while keeping the fraction once the gap reaches a second.
            if abs(epoch) < 1:
                return 0
            return _pg_numeric_div(epoch, Decimal(86400))
        if unit == "week":
            return _pg_numeric_div(epoch, Decimal(86400 * 7))
        months = _sql_month_diff(start, end)
        if unit == "month":
            return months
        if unit == "quarter":
            return _pg_numeric_div(Decimal(months), Decimal("3.0"))
        return _pg_cast_int(months / 12.0)


class FromNow(Func):
    name = "FROMNOW"
    accept_types = (DATETIME, STRING, BOOLEAN)
    accept_multiple = False
    min_params = 2

    def return_type(self, params):
        self.validate(params)
        return (NUMBER, False)

    def eval(self, params, ctx):
        target = _get_dayjs(params[0].value, ctx["timeZone"])
        if target is None:
            return None
        raw = _arg(params, 1)
        key = js_str(raw).strip().lower() if raw is not None else "day"
        unit = _DIFF_UNITS.get(key)
        if unit is None:
            return None
        now = datetime.now(_zone(ctx["timeZone"]))
        # reference is signed now - date (no abs); month/quarter/year use Postgres AGE,
        # the rest use epoch seconds scaled via Postgres numeric division. FROMNOW and
        # TONOW resolve to the same expression.
        if unit in ("month", "quarter", "year"):
            years, months = _pg_age_ym(now, target)
            if unit == "year":
                return years
            diff_months = months + years * 12
            if unit == "month":
                return diff_months
            return _pg_numeric_div(Decimal(diff_months), Decimal("3.0"))
        epoch = (Decimal(round(_ms(now) - _ms(target))) / 1000).quantize(Decimal("0.000001"))
        if unit == "millisecond":
            return float(epoch * 1000)
        if unit == "second":
            return float(epoch)
        if unit == "minute":
            return _pg_numeric_div(epoch, Decimal(60))
        if unit == "hour":
            return _pg_numeric_div(epoch, Decimal(3600))
        if unit == "week":
            return _pg_numeric_div(epoch, Decimal(86400 * 7))
        return _pg_numeric_div(epoch, Decimal(86400))


class ToNow(FromNow):
    name = "TONOW"


class _DateCompare(Func):
    accept_types = (DATETIME, STRING)
    accept_multiple = False
    min_params = 2

    def return_type(self, params):
        self.validate(params)
        return (BOOLEAN, False)

    def _dates(self, params, ctx):
        d1 = _get_dayjs(params[0].value, ctx["timeZone"])
        d2 = _get_dayjs(params[1].value, ctx["timeZone"])
        return d1, d2


class IsSame(_DateCompare):
    name = "IS_SAME"

    def eval(self, params, ctx):
        d1, d2 = self._dates(params, ctx)
        if d1 is None or d2 is None:
            return None
        raw = _arg(params, 2)
        if raw is None:
            return _ms(d1) == _ms(d2)
        unit = _ISSAME_UNITS.get(js_str(raw).strip().lower())
        if unit is None:
            return None
        return _ms(_date_trunc_same(d1, unit)) == _ms(_date_trunc_same(d2, unit))


class IsAfter(_DateCompare):
    name = "IS_AFTER"

    def eval(self, params, ctx):
        d1, d2 = self._dates(params, ctx)
        if d1 is None or d2 is None:
            return None
        return _ms(d1) > _ms(d2)


class IsBefore(_DateCompare):
    name = "IS_BEFORE"

    def eval(self, params, ctx):
        d1, d2 = self._dates(params, ctx)
        if d1 is None or d2 is None:
            return None
        return _ms(d1) < _ms(d2)


class DatetimeFormat(Func):
    name = "DATETIME_FORMAT"
    accept_types = (DATETIME, STRING)
    accept_multiple = False
    min_params = 1

    def return_type(self, params):
        self.validate(params)
        return (STRING, False)

    def eval(self, params, ctx):
        dt = _get_dayjs(params[0].value, ctx["timeZone"])
        if dt is None:
            return None
        raw = _arg(params, 1)
        fmt = js_str(raw) if raw is not None else ""
        if fmt.strip() == "":
            fmt = "YYYY-MM-DD"
        return _format_dayjs(dt, fmt)


class DatetimeParse(Func):
    name = "DATETIME_PARSE"
    accept_types = (DATETIME, STRING)
    accept_multiple = False
    min_params = 1

    def return_type(self, params):
        self.validate(params)
        return (DATETIME, False)

    def eval(self, params, ctx):
        tz = ctx["timeZone"]
        raw_fmt = _arg(params, 1)
        fmt = js_str(raw_fmt) if raw_fmt is not None else None
        if params[0].type == DATETIME and fmt:
            source = _get_dayjs(params[0].value, tz)
            if source is None:
                return None
            reparsed = _get_dayjs(_format_dayjs(source, fmt), tz, fmt)
            return _to_iso_z(reparsed) if reparsed is not None else None
        dt = _get_dayjs(params[0].value, tz, fmt)
        return None if dt is None else _to_iso_z(dt)


def _parse_holidays(raw, tz) -> list[datetime]:
    if not isinstance(raw, str):
        return []
    out: list[datetime] = []
    for part in raw.split(","):
        trimmed = part.strip()
        if not trimmed:
            continue
        try:
            parsed = _get_dayjs(trimmed, tz)
        except FormulaBaseErrorRaised:
            continue
        if parsed is not None:
            out.append(parsed)
    return out


class Workday(Func):
    name = "WORKDAY"
    accept_types = (DATETIME, STRING, NUMBER)
    accept_multiple = False
    min_params = 2

    def return_type(self, params):
        self.validate(params)
        return (DATETIME, False)

    def eval(self, params, ctx):
        tz = ctx["timeZone"]
        start = _get_dayjs(params[0].value, tz)
        if start is None:
            return None
        count = to_number(_arg(params, 1)) if _arg(params, 1) is not None else 0
        holidays = _parse_holidays(_arg(params, 2), tz)
        sign = 1 if count > 0 else -1
        # oracle steps one calendar day at a time, counting only weekdays that
        # are not holidays, until |count| working days have elapsed.
        remaining = abs(int(count))
        target = start
        while remaining > 0:
            target = _add_days(target, sign)
            if _dow(target) not in (0, 6) and not any(_same_day(h, target) for h in holidays):
                remaining -= 1
        return _to_iso_z(target)


class WorkdayDiff(Func):
    name = "WORKDAY_DIFF"
    accept_types = (DATETIME, STRING, NUMBER)
    accept_multiple = False
    min_params = 2

    def return_type(self, params):
        self.validate(params)
        return (NUMBER, False)

    def eval(self, params, ctx):
        tz = ctx["timeZone"]
        start = _get_dayjs(params[0].value, tz)
        end = _get_dayjs(params[1].value, tz)
        if start is None or end is None:
            return None
        holidays = [_start_of(h, "day") for h in _parse_holidays(_arg(params, 2), tz)]
        n_start = _start_of(start, "day")
        n_end = _start_of(end, "day")
        is_forward = _ms(n_end) >= _ms(n_start)
        min_date = n_start if is_forward else n_end
        max_date = n_end if is_forward else n_start
        current = _add_days(min_date, 1)
        count = 0
        while _ms(current) <= _ms(max_date):
            is_weekend = _dow(current) in (0, 6)
            is_holiday = any(_same_day(h, current) for h in holidays)
            if not is_weekend and not is_holiday:
                count += 1
            current = _add_days(current, 1)
        return count if is_forward else -count


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _count_calculator(params, pred) -> int:
    result = 0
    for param in params:
        if param.is_multiple:
            value = param.value
            if not isinstance(value, list):
                result += 1 if pred(value) else 0
                continue
            for item in value:
                if isinstance(item, list):
                    result += sum(1 for x in item if pred(x))
                else:
                    result += 1 if pred(item) else 0
        else:
            result += 1 if pred(param.value) else 0
    return result


class _CountFunc(Func):
    accept_types = (BOOLEAN, DATETIME, NUMBER, STRING)
    accept_multiple = True
    min_params = 1

    def return_type(self, params):
        self.validate(params)
        return (NUMBER, False)


class Count(_CountFunc):
    name = "COUNT"

    def eval(self, params, ctx):
        # oracle COUNT tallies every non-null value (any type), not just numbers.
        return _count_calculator(params, lambda v: v is not None)


class CountA(_CountFunc):
    name = "COUNTA"

    def eval(self, params, ctx):
        return _count_calculator(
            params, lambda v: _is_number(v) or (isinstance(v, str) and v != "")
        )


class CountAll(_CountFunc):
    name = "COUNTALL"
    exact_params = 1

    def eval(self, params, ctx):
        value = params[0].value
        if value is None:
            return 0
        return len(value) if isinstance(value, list) else 1


def _flatten(arr, out) -> None:
    for item in arr:
        if item is None:
            continue
        if isinstance(item, list):
            _flatten(item, out)
        else:
            out.append(item)


def _flatten_params(params) -> list:
    out: list = []
    for param in params:
        value = param.value
        if value is None:
            continue
        if isinstance(value, list):
            _flatten(value, out)
        else:
            out.append(value)
    return out


class ArrayJoin(Func):
    name = "ARRAY_JOIN"
    accept_types = (STRING,)
    accept_multiple = True
    min_params = 1

    def return_type(self, params):
        self.validate(params)
        return (STRING, False)

    def eval(self, params, ctx):
        separator = _arg(params, 1)
        separator = separator if isinstance(separator, str) else ", "
        return convert_value_to_string(params[0], separator)


class _UnionArrayFunc(Func):
    accept_types = (BOOLEAN, DATETIME, NUMBER, STRING)
    accept_multiple = True
    min_params = 1

    def return_type(self, params):
        self.validate(params)
        if not params:
            return (STRING, True)
        first = params[0].type
        same = all(p.type == first for p in params)
        return (first if same else STRING, True)


class ArrayFlatten(_UnionArrayFunc):
    name = "ARRAY_FLATTEN"

    def eval(self, params, ctx):
        flat = _flatten_params(params)
        return flat or None


class ArrayUnique(_UnionArrayFunc):
    name = "ARRAY_UNIQUE"

    def eval(self, params, ctx):
        unique: list = []
        for value in _flatten_params(params):
            if value not in unique:
                unique.append(value)
        return unique or None


class ArrayCompact(_UnionArrayFunc):
    name = "ARRAY_COMPACT"

    def eval(self, params, ctx):
        flat = [value for value in _flatten_params(params) if value != ""]
        return flat or None


class TextAll(Func):
    name = "TEXT_ALL"
    accept_types = (STRING,)
    accept_multiple = True
    exact_params = 1

    def return_type(self, params):
        self.validate(params)
        return (STRING, False)

    def eval(self, params, ctx):
        param = params[0]
        if param.is_multiple:
            value = param.value
            if not value:
                return None
            # oracle joins a multiple value into one string with ", ".
            return ", ".join(
                ", ".join(js_str(x) for x in item) if isinstance(item, list) else js_str(item)
                for item in value
            )
        return param.value or None


class RecordId(Func):
    name = "RECORD_ID"
    accept_types = (STRING,)
    accept_multiple = True

    def return_type(self, params):
        return (STRING, False)

    def eval(self, params, ctx):
        return ctx.get("record_id")


class AutoNumber(Func):
    name = "AUTO_NUMBER"
    accept_types = (STRING,)
    accept_multiple = True

    def return_type(self, params):
        return (NUMBER, False)

    def eval(self, params, ctx):
        return ctx.get("auto_number")


# Fold the extended library into the registry defined above.
_EXTENDED_FUNCTIONS: list[Func] = [
    Even(), Odd(), Exp(), Log(),
    Find(), RegExpReplace(), TextBefore(), TextSplit(), EncodeUrlComponent(),
    Year(), Month(), Day(), Weekday(), WeekNum(), Hour(), Minute(), Second(),
    Datestr(), Timestr(), DateAdd(), DatetimeDiff(), FromNow(), ToNow(),
    IsSame(), IsAfter(), IsBefore(), Workday(), WorkdayDiff(),
    DatetimeFormat(), DatetimeParse(),
    Count(), CountA(), CountAll(),
    ArrayJoin(), ArrayUnique(), ArrayFlatten(), ArrayCompact(),
    TextAll(), RecordId(), AutoNumber(),
]
_FUNCTION_LIST.extend(_EXTENDED_FUNCTIONS)
FUNCTIONS.update({func.name: func for func in _EXTENDED_FUNCTIONS})
