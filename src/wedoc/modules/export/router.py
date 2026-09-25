"""Routes for /api/export — ports export-open-api.controller.ts."""

import json
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, Response

from ...core.security.auth import auth_guard, permissions
from ...core.security.permissions import permission_guard
from .service import ExportService

router = APIRouter(
    prefix="/api/export",
    dependencies=[Depends(auth_guard), Depends(permission_guard)],
)


def _json_param(params: Any, name: str) -> Any:
    raw = params.get(name)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


@router.get("/{tableId}", status_code=200)
@permissions("table|export", "view|read")
async def export_csv_from_table(tableId: str, request: Request) -> Response:
    params = request.query_params
    query: dict[str, Any] = {
        "viewId": params.get("viewId"),
        "ignoreViewQuery": params.get("ignoreViewQuery") in ("true", "1", True),
        "filter": _json_param(params, "filter"),
        "orderBy": _json_param(params, "orderBy"),
        "groupBy": _json_param(params, "groupBy"),
        "projection": params.getlist("projection") or None,
        "columnMeta": _json_param(params, "columnMeta"),
    }
    body = await ExportService().export_csv(tableId, query)
    from ..field.service import FieldService

    table = await FieldService()._load_table(tableId)
    name = table.get("name") if isinstance(table, dict) else None
    filename = quote(name) if name else "export"
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}.csv"},
    )
