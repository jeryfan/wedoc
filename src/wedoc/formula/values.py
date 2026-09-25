"""Runtime value model shared by the evaluator and function library.

``CellValueType`` mirrors the reference ``CellValueType`` enum but uses the
same string literals wedoc stores in ``field.cell_value_type``. ``TypedValue``
mirrors packages/formula TypedValue. Coercion helpers reproduce the JS
semantics the reference visitor relies on (``Number()`` coercion, loose/strict
equality, ``%`` sign, truthiness).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

STRING = "string"
NUMBER = "number"
BOOLEAN = "boolean"
DATETIME = "dateTime"


@dataclass
class TypedValue:
    value: Any
    type: str
    is_multiple: bool = False
    field: dict[str, Any] | None = None
    is_blank: bool = False


class FormulaBaseError:
    """Sentinel produced when a subtree raises and ISERROR swallows it."""

    def __init__(self, message: str = "") -> None:
        self.message = "#ERROR: " + message if message else "#ERROR!"


# VALUES_MARKER


def to_number(value: Any) -> float:
    """Reproduce JS ``Number(value)``: null/blank -> 0, non-numeric -> NaN."""
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped == "":
            return 0.0
        try:
            return float(stripped)
        except ValueError:
            return math.nan
    return math.nan


def js_truthy(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0 and not math.isnan(value)
    if isinstance(value, str):
        return len(value) > 0
    if isinstance(value, (list, tuple, dict)):
        return True
    return bool(value)


def js_mod(a: float, b: float) -> float:
    return math.fmod(a, b)


def normalize_number_output(value: Any) -> Any:
    """Collapse NaN/inf to None and integral floats to int (VO parity)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        if value.is_integer():
            return int(value)
    return value


def relational(left: Any, right: Any, op: str) -> bool:
    if isinstance(left, str) and isinstance(right, str):
        a: Any = left
        b: Any = right
    else:
        a = to_number(left)
        b = to_number(right)
        if isinstance(a, float) and math.isnan(a):
            return False
        if isinstance(b, float) and math.isnan(b):
            return False
    if op == ">":
        return a > b
    if op == "<":
        return a < b
    if op == ">=":
        return a >= b
    return a <= b


def _looks_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def loose_eq(a: Any, b: Any) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if isinstance(a, bool) or isinstance(b, bool):
        return to_number(a) == to_number(b)
    if _looks_number(a) and _looks_number(b):
        return a == b
    if isinstance(a, str) and isinstance(b, str):
        return a == b
    if _looks_number(a) or _looks_number(b):
        na, nb = to_number(a), to_number(b)
        if math.isnan(na) or math.isnan(nb):
            return False
        return na == nb
    return a == b


def strict_eq(a: Any, b: Any) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if _looks_number(a) and _looks_number(b):
        return a == b
    if isinstance(a, str) and isinstance(b, str):
        return a == b
    return False


# DATETIME_MARKER

_DATE_TOKENS = {
    "M/D/YYYY": "%-m/%-d/%Y",
    "D/M/YYYY": "%-d/%-m/%Y",
    "YYYY/MM/DD": "%Y/%m/%d",
    "YYYY-MM-DD": "%Y-%m-%d",
    "YYYY-MM": "%Y-%m",
    "MM-DD": "%m-%d",
    "YYYY": "%Y",
    "MM": "%m",
    "DD": "%d",
}
_TIME_TOKENS = {"HH:mm": "%H:%M", "hh:mm A": "%I:%M %p"}


def _field_formatting(field: dict[str, Any] | None) -> dict[str, Any] | None:
    if not field:
        return None
    import json

    raw = field.get("options")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except ValueError:
            return None
    if isinstance(raw, dict):
        fmt = raw.get("formatting")
        return fmt if isinstance(fmt, dict) else None
    return None


def format_datetime(iso_value: str, field: dict[str, Any] | None) -> str:
    fmt = _field_formatting(field) or {}
    date_fmt = _DATE_TOKENS.get(fmt.get("date", "YYYY-MM-DD"), "%Y-%m-%d")
    time_fmt = _TIME_TOKENS.get(fmt.get("time"))
    tz_name = fmt.get("timeZone")
    try:
        parsed = datetime.fromisoformat(iso_value.replace("Z", "+00:00"))
        if tz_name:
            from zoneinfo import ZoneInfo

            parsed = parsed.astimezone(ZoneInfo(tz_name))
        pattern = f"{date_fmt} {time_fmt}" if time_fmt else date_fmt
        return parsed.strftime(pattern)
    except Exception:
        return iso_value


def js_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    return str(value)


def cell_value_to_string(field: dict[str, Any] | None, value: Any) -> str:
    if value is None:
        return ""
    cvt = field.get("cell_value_type") if field else None
    if cvt == DATETIME and isinstance(value, str):
        return format_datetime(value, field)
    if isinstance(value, dict):
        return value.get("title") or value.get("name") or ""
    if isinstance(value, list):
        return ", ".join(cell_value_to_string(field, item) for item in value)
    return js_str(value)


def convert_value_to_string(param: TypedValue | None, separator: str = ", ") -> str | None:
    if param is None:
        return None
    value = param.value
    if value is None:
        return None
    if param.field and param.field.get("cell_value_type") == DATETIME:
        if param.is_multiple and isinstance(value, list):
            return separator.join(cell_value_to_string(param.field, item) for item in value)
        return cell_value_to_string(param.field, value)
    if param.is_multiple and isinstance(value, list):
        return separator.join("" if item is None else js_str(item) for item in value)
    return js_str(value)
