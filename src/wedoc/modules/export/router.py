"""Routes for /api/export — ports export-open-api.controller.ts."""

import json
from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from ...core.errors import ApiError, HttpErrorCode
from ...core.query import query_list
from ...core.security.auth import auth_guard, permissions
from ...core.security.permissions import permission_guard
from ..table import repository as table_repository
from .service import ExportService


async def _require_export_table(request: Request) -> None:
    # Export resolves the table itself: a missing table reports the reference's
    # 404 "Invalid tableId" before the shared permission guard's 403 fires.
    table_id = request.path_params.get("tableId")
    if table_id and not await table_repository.table_exists_by_id(table_id):
        raise ApiError(
            f"Invalid tableId: {table_id}",
            HttpErrorCode.NOT_FOUND,
            {"localization": {"i18nKey": "httpErrors.table.notFound"}},
        )


router = APIRouter(
    prefix="/api/export",
    dependencies=[Depends(auth_guard), Depends(_require_export_table), Depends(permission_guard)],
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
        "projection": query_list(params, "projection") or None,
        "columnMeta": _json_param(params, "columnMeta"),
    }
    body, filename = await ExportService().export_csv(tableId, query)
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}.csv"},
    )
