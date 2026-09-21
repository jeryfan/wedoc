"""Parse the `ms`-package duration format ('20d', '30m', '1h', '500') to milliseconds."""

import re

_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*(ms|s|m|h|d|w|y)?\s*$", re.IGNORECASE)

_UNIT_MS = {
    "ms": 1,
    "s": 1000,
    "m": 60_000,
    "h": 3_600_000,
    "d": 86_400_000,
    "w": 604_800_000,
    "y": 31_536_000_000,
}


def parse_ms(value: str | int | float | None, default: int | None = None) -> int:
    if value is None:
        if default is None:
            raise ValueError("duration value required")
        return default
    if isinstance(value, int | float):
        return int(value)
    match = _RE.match(value)
    if not match:
        if default is not None:
            return default
        raise ValueError(f"invalid duration: {value!r}")
    amount = float(match.group(1))
    unit = (match.group(2) or "ms").lower()
    return int(amount * _UNIT_MS[unit])
