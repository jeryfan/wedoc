"""Cell-value typecasting for the selection paste family.

Ports the reference `TypeCastAndValidate` (record/typecast.validate.ts) for the
field types that are self-contained (no link/collaborator/attachment resolution):
text, long text, number, rating, checkbox, date, single/multiple select. Select
fields auto-create missing choices on the field, matching the reference paste
behavior. Link/user/attachment fall back to the reference's cross-type string
coercion (they need the link/collaborator engines from the B group).
"""

import re
from datetime import UTC, datetime
from typing import Any

from ...core.ids import IdPrefix, new_id
from ..field.service import COLORS

_NUM_STRIP = re.compile(r"[^\d.+-]")
_NUM_SYMBOLS = re.compile(r"([+\-.])+")
_LEADING_FLOAT = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)")


def _js_parse_float(num_str: str) -> float | None:
    match = _LEADING_FLOAT.match(num_str)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def parse_string_to_number(value: Any, formatting: dict[str, Any] | None) -> float | int | None:
    if value is None or value == "":
        return None
    origin = str(value)
    is_percent = (formatting or {}).get("type") == "percent" or "%" in origin
    num_str = _NUM_STRIP.sub("", origin)
    num_str = _NUM_SYMBOLS.sub(lambda m: m.group(0)[-1], num_str)
    num = _js_parse_float(num_str)
    if num is None:
        return None
    result = num / 100 if is_percent else num
    if isinstance(result, float) and result.is_integer():
        return int(result)
    return result


def _text_convert(value: Any) -> str | None:
    if value is None:
        return None
    real = re.sub(r"[\n\r\t]", " ", str(value)).strip()
    return real or None


def _checkbox_repair(value: Any) -> bool | None:
    if isinstance(value, bool):
        return True if value else None
    if isinstance(value, str):
        low = value.lower()
        if low == "true":
            return True
        if low == "false":
            return None
    return True if value else None


def _date_convert(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        raw = value.strip()
    else:
        raw = str(value)
    if raw == "now":
        return datetime.now(UTC).isoformat().replace("+00:00", "Z")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(raw, fmt).replace(tzinfo=UTC)
            return parsed.isoformat().replace("+00:00", "Z")
        except ValueError:
            continue
    return None


def _value_to_string_array(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, list):
        return [str(v).strip() for v in value if v is not None and v != ""]
    trimmed = str(value).strip()
    return [trimmed] if trimmed else None


def _random_colors(used: list[str], count: int) -> list[str]:
    available = [c for c in COLORS if c not in set(used)]
    out: list[str] = []
    for i in range(count):
        out.append(available[i] if i < len(available) else COLORS[i % len(COLORS)])
    return out


class FieldTypecaster:
    """Casts a single field's column of raw clipboard values, creating select
    options as needed (persisted to the field row)."""

    def __init__(self, field: dict[str, Any]) -> None:
        self.field = field
        self.type = field["type"]
        self._pending_choice_names: list[str] = []

    def _formatting(self) -> dict[str, Any] | None:
        return (self.field.get("options") or {}).get("formatting")

    def cast(self, values: list[Any]) -> list[Any]:
        t = self.type
        if t in ("singleLineText", "longText"):
            return [_text_convert(v) for v in values]
        if t in ("number", "rating"):
            return [parse_string_to_number(v, self._formatting()) for v in values]
        if t == "checkbox":
            return [_checkbox_repair(v) for v in values]
        if t in ("date", "createdTime", "lastModifiedTime"):
            return [_date_convert(v) for v in values]
        if t == "singleSelect":
            return self._cast_single_select(values)
        if t == "multipleSelect":
            return self._cast_multiple_select(values)
        # link/user/attachment and other computed-ish types: best-effort string.
        return [None if v is None else (v if isinstance(v, str) else str(v)) for v in values]

    def _existing_choice_names(self) -> set[str]:
        choices = (self.field.get("options") or {}).get("choices") or []
        return {c["name"] for c in choices}

    def _cast_single_select(self, values: list[Any]) -> list[Any]:
        prevent = bool((self.field.get("options") or {}).get("preventAutoNewOptions"))
        existing = self._existing_choice_names()
        out: list[Any] = []
        new_names: list[str] = []
        for v in values:
            arr = _value_to_string_array(v)
            name = arr[0] if arr else None
            out.append(name)
            if name and name not in existing and name not in new_names:
                new_names.append(name)
        if prevent:
            return [None if (n is not None and n not in existing) else n for n in out]
        self._pending_choice_names = new_names
        return out

    def _cast_multiple_select(self, values: list[Any]) -> list[Any]:
        prevent = bool((self.field.get("options") or {}).get("preventAutoNewOptions"))
        existing = self._existing_choice_names()
        out: list[Any] = []
        new_names: list[str] = []
        for v in values:
            if isinstance(v, str):
                arr = [s.strip() for s in v.split(",")]
            elif isinstance(v, list):
                arr = [str(s).strip() for s in v if isinstance(s, str)]
            else:
                arr = None
            names = [n for n in arr if n] if arr else None
            out.append(names or None)
            for n in names or []:
                if n not in existing and n not in new_names:
                    new_names.append(n)
        if prevent:
            return [
                [n for n in cell if n in existing] if isinstance(cell, list) else cell
                for cell in out
            ]
        self._pending_choice_names = new_names
        return out

    async def flush_new_choices(self, table_id: str) -> list[str]:
        """Persist newly-seen select choices to the field; returns created ids."""
        if not self._pending_choice_names:
            return []
        import json as _json

        from ..field import repository as field_repo

        row = await field_repo.get_field_row(table_id, self.field["id"])
        if row is None:
            self._pending_choice_names = []
            return []
        options = _json.loads(row.get("options") or "{}")
        choices = list(options.get("choices") or [])
        used_colors = [c.get("color") for c in choices if c.get("color")]
        colors = _random_colors(used_colors, len(self._pending_choice_names))
        created_ids: list[str] = []
        for name, color in zip(self._pending_choice_names, colors, strict=True):
            cid = new_id(IdPrefix.CHOICE, 8)
            choices.append({"id": cid, "name": name, "color": color})
            created_ids.append(cid)
        options["choices"] = choices
        await field_repo.update_field_row(
            self.field["id"],
            {
                "options": _json.dumps(options, separators=(",", ":")),
                "version": row["version"] + 1,
                "last_modified_time": datetime.now(UTC).replace(tzinfo=None),
            },
        )
        self.field["options"] = options
        self._pending_choice_names = []
        return created_ids
