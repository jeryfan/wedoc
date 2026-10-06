"""CSV export service — ports export-open-api.service.ts (legacy V1).

Streams a table's records as CSV: BOM + header row, then batches of records
joined by CRLF, mirroring the reference's Papa.unparse output. cellValue2String
matches the reference for text types (the strictly parity-verified path);
numeric/date formatting reuses the record VO values.
"""

import json
from typing import Any
from urllib.parse import quote

from ...core.errors import ApiError, HttpErrorCode
from .. import table as _table_pkg  # noqa: F401  (ensure package import side effects)
from ..field.service import FieldService
from ..record.service import RecordService
from ..view.repository import get_view_row

DEFAULT_TAKE = 1000


def papa_unparse(rows: list[list[Any]]) -> str:
    """Equivalent of Papa.unparse: quote fields containing the delimiter, a
    quote, CR or LF; escape embedded quotes by doubling; join rows with CRLF."""
    out_lines: list[str] = []
    for row in rows:
        cells: list[str] = []
        for cell in row:
            text = "" if cell is None else _scalar(cell)
            if any(c in text for c in (",", '"', "\r", "\n")):
                text = '"' + text.replace('"', '""') + '"'
            cells.append(text)
        out_lines.append(",".join(cells))
    return "\r\n".join(out_lines)


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _format_number(value: Any, formatting: dict[str, Any] | None) -> str:
    try:
        num = float(value)
    except (ValueError, TypeError):
        return cell_value_to_string_scalar(value)
    fmt = formatting or {}
    precision = fmt.get("precision", 2)
    ftype = fmt.get("type", "decimal")
    if ftype == "percent":
        return f"{num * 100:.{precision}f}%"
    if ftype == "currency":
        symbol = fmt.get("symbol", "$")
        return f"{symbol}{num:.{precision}f}"
    return f"{num:.{precision}f}"


def cell_value_to_string(field: dict[str, Any], value: Any) -> str:
    if value is None:
        return ""
    ftype = field["type"]
    if ftype == "attachment" and isinstance(value, list):
        return ",".join(f"{v.get('name')} {v.get('presignedUrl')}" for v in value)
    if ftype in ("number",):
        options = field.get("options") or {}
        return _format_number(value, options.get("formatting"))
    if isinstance(value, list):
        return ", ".join(cell_value_to_string_scalar(v) for v in value)
    return cell_value_to_string_scalar(value)


def cell_value_to_string_scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict):
        title = value.get("title")
        return "" if title is None else str(title)
    return str(value)


class ExportService:
    async def _visible_fields(
        self, table_id: str, view_id: str | None, ignore_view_query: bool,
        column_meta: dict[str, Any] | None
    ) -> list[dict[str, Any]]:
        fields = await FieldService().list_fields(table_id)
        if view_id and not ignore_view_query:
            view = await get_view_row(table_id, view_id)
            if view is not None:
                cmeta = _json(view.get("column_meta"))
                options = _json(view.get("options"))
                vtype = view["type"]
                from ..selection.service import _is_not_hidden

                fields = [
                    f for f in fields if _is_not_hidden(f["id"], vtype, cmeta, options)
                ]
                fields.sort(key=lambda f: (cmeta.get(f["id"]) or {}).get("order", 0))
        elif ignore_view_query and column_meta:
            fields.sort(
                key=lambda f: (column_meta.get(f["id"]) or {}).get("order", float("inf"))
            )
        return fields

    async def export_csv(self, table_id: str, query: dict[str, Any]) -> str:
        table = await FieldService()._load_table(table_id)  # raises 404 if missing
        view_id = query.get("viewId")
        ignore_view_query = bool(query.get("ignoreViewQuery"))
        view_row = None
        if view_id and not ignore_view_query:
            view_row = await get_view_row(table_id, view_id)
            # ref: `viewRaw?.type !== Grid` throws — a missing/foreign viewId yields
            # an undefined type and 400s too (no silent full-table export).
            if view_row is None or view_row["type"] != "grid":
                view_type = view_row["type"] if view_row else "undefined"
                # JS `{ viewType: undefined }` serializes with the key omitted, so
                # a missing view yields an empty context (not viewType: null).
                context = {"viewType": view_row["type"]} if view_row else {}
                raise ApiError(
                    f"{view_type} is not support to export",
                    HttpErrorCode.VALIDATION_ERROR,
                    {
                        "localization": {
                            "i18nKey": "httpErrors.export.notSupportViewType",
                            "context": context,
                        }
                    },
                )

        view_id_for_query = None if ignore_view_query else (
            view_row["id"] if view_row else None
        )
        fields = await self._visible_fields(
            table_id, view_id, ignore_view_query, query.get("columnMeta")
        )

        projection = query.get("projection")
        headers = [f for f in fields if not projection or f["id"] in projection]
        header_names = [h["name"] for h in headers]

        chunks: list[str] = ["\ufeff", papa_unparse([header_names])]

        count = 0
        while True:
            data = await RecordService().list_records(
                table_id,
                field_key_type="name",
                view_id=view_id_for_query,
                filter_param=query.get("filter"),
                order_by=query.get("orderBy"),
                take=DEFAULT_TAKE,
                skip=count,
            )
            records = data["records"]
            if not records:
                break
            rows: list[list[Any]] = []
            for rec in records:
                fvals = rec["fields"]
                rows.append(
                    [cell_value_to_string(h, fvals.get(h["name"])) for h in headers]
                )
            chunks.append("\r\n")
            chunks.append(papa_unparse(rows))
            count += len(records)

        _ = table
        table_name = table.get("name") if isinstance(table, dict) else None
        if table_name:
            suffix = f"_{view_row['name']}" if view_row else ""
            filename = quote(f"{table_name}{suffix}")
        else:
            filename = "export"
        return "".join(chunks), filename


def _json(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return raw
