"""cellFormat=text rendering: field-typed cellValue2String parity.

Mirrors packages/core field ``cellValue2String`` per field type over the JSON
record VO values. Number/date honor the reference formatting presets; select
and user items quote values containing a comma only when the cell is
multi-valued, matching the reference ``item2String``.
"""

import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

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


def _formatting(field: dict[str, Any]) -> dict[str, Any] | None:
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


def _scalar(value: Any) -> str:
    # reproduces JS String(value): booleans lower-cased, integral floats un-dotted.
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _format_number(value: Any, formatting: dict[str, Any] | None) -> str:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return _scalar(value)
    fmt = formatting or {"type": "decimal", "precision": 2}
    ftype = fmt.get("type", "decimal")
    precision = fmt.get("precision")
    precision = 2 if precision is None else int(precision)
    if ftype == "currency":
        symbol = fmt.get("symbol", "$")
        sign = "-" if num < 0 else ""
        return f"{sign}{symbol}{abs(num):,.{precision}f}"
    if ftype == "percent":
        return f"{num * 100:.{precision}f}%"
    return f"{num:.{precision}f}"


def _format_date(value: Any, formatting: dict[str, Any] | None) -> str:
    if not isinstance(value, str):
        return _scalar(value)
    fmt = formatting or {}
    date_fmt = _DATE_TOKENS.get(fmt.get("date", "YYYY-MM-DD"), "%Y-%m-%d")
    time_token = fmt.get("time")
    pattern = date_fmt
    if time_token and time_token != "None":
        pattern = f"{date_fmt} {_TIME_TOKENS.get(time_token, '%H:%M')}"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    tz_name = fmt.get("timeZone")
    if tz_name:
        try:
            parsed = parsed.astimezone(ZoneInfo(tz_name))
        except Exception:
            pass
    return parsed.strftime(pattern)


def _select_item(value: Any, multiple: bool) -> str:
    text = _scalar(value)
    if multiple and "," in text:
        return f'"{text}"'
    return text


def _user_item(value: Any, multiple: bool) -> str:
    title = value.get("title") if isinstance(value, dict) else None
    if multiple and title and "," in title:
        return f'"{title}"'
    return title or ""


def _link_item(value: Any) -> str:
    if isinstance(value, dict):
        return value.get("title") or ""
    return ""


def _attachment_item(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    return f"{value.get('name', '')} ({value.get('token', '')})"


def _by_cell_value_type(value: Any, cvt: str | None, formatting: dict[str, Any] | None) -> str:
    if cvt == "number":
        return _format_number(value, formatting)
    if cvt == "dateTime":
        return _format_date(value, formatting)
    return _scalar(value)


def cell_value_to_string(field: dict[str, Any], value: Any) -> str:
    if value is None:
        return ""
    ftype = field.get("type")
    multiple = bool(field.get("is_multiple_cell_value"))

    if ftype == "number":
        formatting = _formatting(field)
        if isinstance(value, list):
            return ", ".join(_format_number(v, formatting) for v in value)
        return _format_number(value, formatting)

    if ftype in ("date", "createdTime", "lastModifiedTime"):
        formatting = _formatting(field)
        if isinstance(value, list):
            return ", ".join(_format_date(v, formatting) for v in value)
        return _format_date(value, formatting)

    if ftype in ("singleSelect", "multipleSelect"):
        if isinstance(value, list):
            return ", ".join(_select_item(v, multiple) for v in value)
        return _scalar(value)

    if ftype in ("user", "createdBy", "lastModifiedBy"):
        if isinstance(value, list):
            return ", ".join(_user_item(v, multiple) for v in value)
        return _user_item(value, multiple)

    if ftype == "link":
        if isinstance(value, list):
            return ", ".join(_link_item(v) for v in value)
        return _link_item(value)

    if ftype == "attachment":
        if isinstance(value, list):
            return ",".join(_attachment_item(v) for v in value)
        return ""

    if ftype in ("formula", "rollup", "conditionalRollup"):
        cvt = field.get("cell_value_type")
        formatting = _formatting(field)
        if multiple and isinstance(value, list):
            return ", ".join(_by_cell_value_type(v, cvt, formatting) for v in value)
        return _by_cell_value_type(value, cvt, formatting)

    if ftype == "button":
        return ""

    if isinstance(value, list):
        return ", ".join(_scalar(v) for v in value)
    return _scalar(value)
