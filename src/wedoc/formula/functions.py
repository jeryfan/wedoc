"""Formula function library + argument type converter.

Ports the reference ``FUNCTIONS`` factory (the common subset) and
``TypedValueConverter``. Each function exposes ``accept_types`` (ordered, the
first entry is the coercion target), ``accept_multiple`` and the
``return_type`` / ``eval`` pair the evaluator drives. Date/time, array, system
and the rarer text/numeric helpers are intentionally not registered — see
``docs`` note in the module and the task handoff for the deferred list.
"""

from __future__ import annotations

import math
import re
from typing import Any

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
        return value[start:start + count]


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
    Replace(), Substitute(), T(),
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

