"""File fetch + parse + column type detection — ports import.class.ts.

Supports the two analyze/import file types (csv, excel). CSV uses the stdlib
csv module (with chardet encoding detection); Excel uses openpyxl. Column type
detection reproduces Importer.genColumns for the five validate types.
"""

from __future__ import annotations

import csv
import io
import re
from typing import Any

import chardet
import httpx

from ...config import get_settings
from ...core.errors import ApiError, HttpErrorCode

CSV_DEFAULT_SHEETKEY = "Import Table"
CHECK_LINES = 500

# Importer.SUPPORTEDTYPE order matters (first match wins).
SUPPORTED_TYPES = ["checkbox", "number", "date", "longText", "singleLineText"]
DEFAULT_COLUMN_TYPE = "singleLineText"

_DATE_PATTERNS = [
    re.compile(r"^\d{4}-\d{2}-\d{2}$"),
    re.compile(r"^\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}(?::\d{2})?(?:\.\d{1,3})?$"),
    re.compile(
        r"^\d{4}-\d{2}-\d{2}T\d{1,2}:\d{2}(?::\d{2})?(?:\.\d{1,3})?(?:Z|[+-]\d{2}:?\d{2})?$"
    ),
    re.compile(r"^\d{1,2}-\d{1,2}-\d{4}$"),
    re.compile(r"^\d{4}/\d{1,2}/\d{1,2}$"),
    re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$"),
    re.compile(r"^\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}(?::\d{2})?$"),
]
_FLOAT_RE = re.compile(r"^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$")


def _dynamic_type(value: str) -> Any:
    if value == "true":
        return True
    if value == "false":
        return False
    if value != "" and _FLOAT_RE.match(value):
        try:
            if "." in value or "e" in value or "E" in value:
                return float(value)
            return int(value)
        except ValueError:
            return value
    return value


def _is_valid_date_for_import(value: Any) -> bool:
    if value == "" or value is None:
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # epoch-ms parse; keep within a sane year range like the reference.
        return True
    if not isinstance(value, str):
        return False
    s = value.strip()
    if not s:
        return False
    return any(p.match(s) for p in _DATE_PATTERNS)


def _validate_type(field_type: str, value: Any) -> bool:
    if field_type == "checkbox":
        if isinstance(value, bool):
            return True
        return isinstance(value, str) and value.lower() in ("true", "false")
    if field_type == "date":
        return _is_valid_date_for_import(value)
    if field_type == "number":
        if isinstance(value, bool):
            return False
        if isinstance(value, (int, float)):
            return True
        try:
            float(str(value))
            return True
        except (ValueError, TypeError):
            return False
    if field_type == "longText":
        return isinstance(value, str) and "\n" in value
    if field_type == "singleLineText":
        return isinstance(value, str)
    return False


def _get_uniq_name(name: str, existing: list[str]) -> str:
    if name not in existing:
        return name
    i = 1
    while f"{name} ({i})" in existing:
        i += 1
    return f"{name} ({i})"


def _resolve_url(attachment_url: str) -> str:
    trimmed = attachment_url.strip()
    if trimmed.startswith("http://") or trimmed.startswith("https://"):
        return trimmed
    port = get_settings().port
    return f"http://localhost:{port}{trimmed}"


def _parse_content_disposition(disposition: str | None) -> str | None:
    if not disposition:
        return None
    m = re.search(r"filename\*=UTF-8''([^;]+)", disposition) or re.search(
        r'filename="?([^"]+)"?', disposition
    )
    if m:
        from urllib.parse import unquote

        return unquote(m.group(1))
    return None


async def fetch_file(attachment_url: str) -> tuple[bytes, str]:
    url = _resolve_url(attachment_url)
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url)
        if resp.status_code >= 400:
            raise ApiError(
                f"Failed to fetch import file: {resp.status_code}",
                HttpErrorCode.VALIDATION_ERROR,
            )
        data = resp.content
        filename = _parse_content_disposition(resp.headers.get("content-disposition"))
    default_name = "Import Table"
    if filename:
        default_name = filename.split(".")[0]
    return data, default_name


def _decode_csv(data: bytes) -> str:
    detected = chardet.detect(data[: 64 * 1024])
    encoding = detected.get("encoding") or "utf-8"
    if encoding.lower() in ("ascii", "utf-8"):
        encoding = "utf-8"
    try:
        return data.decode(encoding, errors="replace")
    except (LookupError, UnicodeDecodeError):
        return data.decode("utf-8", errors="replace")


def parse_csv_rows(data: bytes, *, dynamic: bool, limit: int | None) -> list[list[Any]]:
    text = _decode_csv(data)
    # strip a leading BOM the stdlib reader would otherwise keep on cell 0
    if text.startswith("\ufeff"):
        text = text[1:]
    reader = csv.reader(io.StringIO(text))
    rows: list[list[Any]] = []
    for i, row in enumerate(reader):
        if limit is not None and i >= limit:
            break
        if dynamic:
            rows.append([_dynamic_type(c) for c in row])
        else:
            rows.append(list(row))
    return rows


def parse_excel_sheets(data: bytes) -> dict[str, list[list[Any]]]:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    result: dict[str, list[list[Any]]] = {}
    for name in wb.sheetnames:
        ws = wb[name]
        rows: list[list[Any]] = []
        for row in ws.iter_rows(values_only=True):
            rows.append(["" if c is None else c for c in row])
        result[name] = rows
    wb.close()
    return result


def _gen_columns_for_sheet(rows: list[list[Any]]) -> list[dict[str, str]]:
    if not rows:
        return []
    width = max((len(r) for r in rows), default=0)
    existing: list[str] = []
    columns: list[dict[str, str]] = []
    for index in range(width):
        column = [r[index] if index < len(r) else None for r in rows]
        is_empty = True
        validating = list(SUPPORTED_TYPES)
        for i, value in enumerate(column):
            if len(validating) <= 1:
                break
            if value == "" or value is None or i == 0:
                continue
            is_empty = False
            if _validate_type("longText", value):
                validating = ["longText"]
                break
            validating = [t for t in validating if _validate_type(t, value)]
        if is_empty:
            validating = [DEFAULT_COLUMN_TYPE]
        header = str(column[0]).strip() if column and column[0] is not None else ""
        name = _get_uniq_name(header or f"Field {index}", existing)
        existing.append(name)
        columns.append({"type": validating[0] if validating else DEFAULT_COLUMN_TYPE,
                        "name": str(name)})
    return columns


async def gen_columns(file_type: str, attachment_url: str) -> dict[str, Any]:
    data, default_name = await fetch_file(attachment_url)
    worksheets: dict[str, Any] = {}
    if file_type == "csv":
        rows = parse_csv_rows(data, dynamic=True, limit=CHECK_LINES)
        columns = _gen_columns_for_sheet(rows)
        worksheets[CSV_DEFAULT_SHEETKEY] = {"name": default_name, "columns": columns}
    else:
        sheets = parse_excel_sheets(data)
        for sheet_name, rows in sheets.items():
            columns = _gen_columns_for_sheet(rows)
            worksheets[sheet_name] = {"name": sheet_name, "columns": columns}
    return {"worksheets": worksheets}
