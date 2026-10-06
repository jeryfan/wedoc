"""ID generation matching upstream formats exactly.

Domain ids: 3-char prefix + random string from the alphabet
`0-9a-zA-Z` (default 16 chars). Prisma-style row ids: cuid v1.
"""

import os
import re
import secrets
import time
from enum import StrEnum

CHARS = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
CHARS_NUMBER = "0123456789"


class IdPrefix(StrEnum):
    SPACE = "spc"
    BASE = "bse"
    BASE_NODE = "bno"
    BASE_NODE_FOLDER = "bnf"
    TABLE = "tbl"
    FIELD = "fld"
    VIEW = "viw"
    RECORD = "rec"
    COMMENT = "com"
    ATTACHMENT = "act"
    CHOICE = "cho"
    WORKFLOW = "wfl"
    WORKFLOW_TRIGGER = "wtr"
    WORKFLOW_ACTION = "wac"
    WORKFLOW_DECISION = "wde"
    USER = "usr"
    ACCOUNT = "aco"
    INVITATION = "inv"
    SHARE = "shr"
    NOTIFICATION = "not"
    ACCESS_TOKEN = "acc"
    AUTHORITY_MATRIX = "aut"
    AUTHORITY_MATRIX_ROLE = "aur"
    LICENSE = "lic"
    OAUTH_CLIENT = "clt"
    WINDOW = "win"
    RECORD_HISTORY = "rhi"
    PLUGIN = "plg"
    PLUGIN_INSTALL = "pli"
    PLUGIN_USER = "plu"
    PLUGIN_PANEL = "plp"
    DASHBOARD = "dsh"
    RECORD_TRASH = "rtr"
    OPERATION = "opr"
    ORGANIZATION = "org"
    ORGANIZATION_DEPARTMENT = "odp"
    INTEGRATION = "int"
    TEMPLATE = "tpl"
    TEMPLATE_CATEGORY = "tpc"
    TASK = "tsk"
    TASK_RUN = "trn"
    CHAT = "cht"
    CHAT_MESSAGE = "cmm"
    QUERY = "qry"
    APP = "app"
    AI_PROXY_TOKEN = "apt"


def random_string(length: int, numeric: bool = False) -> str:
    alphabet = CHARS_NUMBER if numeric else CHARS
    return "".join(secrets.choice(alphabet) for _ in range(length))


def new_id(prefix: IdPrefix, length: int = 16) -> str:
    return str(prefix) + random_string(length)


def identify(id_: str) -> IdPrefix | None:
    if len(id_) < 3:
        return None
    try:
        return IdPrefix(id_[:3])
    except ValueError:
        return None


def is_valid_prefixed_id(value: object, prefix: str, allow_suffix: bool = False) -> bool:
    """Prefixed-id format check: ``<prefix>`` + 1-64 alphanumerics, optionally a
    ``_<n>`` suffix (derived field ids). Parsing stays looser than generation so
    legacy/imported ids of other body lengths still pass, matching the reference
    id value-objects (RecordId/FieldId/ViewId)."""
    if not isinstance(value, str):
        return False
    suffix = r"(?:_\d+)?" if allow_suffix else ""
    return re.fullmatch(rf"{re.escape(prefix)}[0-9a-zA-Z]{{1,64}}{suffix}", value) is not None


_cuid_counter = int.from_bytes(os.urandom(2), "big") % 1679616
_cuid_fingerprint = "".join(
    secrets.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(4)
)


def _base36(num: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    if num == 0:
        return "0"
    out = ""
    while num:
        num, rem = divmod(num, 36)
        out = digits[rem] + out
    return out


def cuid() -> str:
    """cuid v1: c + ts(b36) + counter(b36,4) + fingerprint(4) + random(8)."""
    global _cuid_counter
    _cuid_counter = (_cuid_counter + 1) % 1679616
    ts = _base36(int(time.time() * 1000))
    counter = _base36(_cuid_counter).rjust(4, "0")
    rand = "".join(secrets.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(8))
    return f"c{ts}{counter}{_cuid_fingerprint}{rand}"
