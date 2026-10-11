"""The reference's "server resolved timezone" (Node ``Intl``).

Node resolves the process timezone from ``TZ`` when set, otherwise from the
host's ``/etc/localtime``. Field defaults (date formatting, formula results)
embed that name in the stored options, so it has to be reproduced rather than
hard-coded.
"""

from __future__ import annotations

import os
from functools import cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_FALLBACK = "UTC"
_ZONEINFO_MARKER = "zoneinfo/"


def _valid(name: str | None) -> str | None:
    if not name:
        return None
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return name


@cache
def server_timezone() -> str:
    """TZ env -> /etc/localtime -> UTC, matching what Node reports."""
    from_env = _valid(os.environ.get("TZ"))
    if from_env:
        return from_env
    try:
        link = os.readlink("/etc/localtime")
    except OSError:
        return _FALLBACK
    marker = link.rfind(_ZONEINFO_MARKER)
    if marker >= 0:
        return _valid(link[marker + len(_ZONEINFO_MARKER):]) or _FALLBACK
    return _FALLBACK
