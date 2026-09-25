"""Plugin chart service — ports plugin-chart.service.ts.

Resolves the stored base query of an installed plugin and runs it through the
base query engine (``base.base_query.BaseQueryService``), returning the same
``{rows, columns}`` view object as the reference for both the dashboard and
plugin-panel chart surfaces.
"""

from typing import Any

from ...core.errors import ApiError, HttpErrorCode
from ..dashboard.service import DashboardService

_CELL_FORMATS = ("json", "text")


def _validate_cell_format(cell_format: str | None) -> str:
    if cell_format is None:
        return "text"
    if cell_format not in _CELL_FORMATS:
        # cellFormat is validated by the query-schema ZodValidationPipe upstream,
        # so the wire message carries the zod-validation-error wrapper.
        raise ApiError(
            'Validation error: Error cellFormat, You should set it to "json" or'
            ' "text" at "cellFormat"',
            HttpErrorCode.VALIDATION_ERROR,
        )
    return cell_format


class PluginChartService:
    async def dashboard_query(
        self, plugin_install_id: str, position_id: str, base_id: str, cell_format: str | None
    ) -> dict[str, Any]:
        resolved_format = _validate_cell_format(cell_format)
        install = await DashboardService().get_plugin_install(
            base_id, position_id, plugin_install_id
        )
        query = (install.get("storage") or {}).get("query")
        if not query:
            raise ApiError(
                "Dashboard Plugin Storage Query not found",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.pluginChart.queryNotFound"}},
            )
        from ..base.base_query import BaseQueryService

        return await BaseQueryService().base_query(base_id, query, resolved_format)

    async def plugin_panel_query(
        self, plugin_install_id: str, position_id: str, table_id: str, cell_format: str | None
    ) -> dict[str, Any]:
        from ..plugin_panel.service import PluginPanelService

        resolved_format = _validate_cell_format(cell_format)
        install = await PluginPanelService().get_plugin(
            table_id, position_id, plugin_install_id
        )
        query = (install.get("storage") or {}).get("query")
        if not query:
            raise ApiError(
                "Plugin Panel Plugin Storage Query not found",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.pluginChart.queryNotFound"}},
            )
        from ..base.base_query import BaseQueryService

        base_id = install["baseId"]
        return await BaseQueryService().base_query(base_id, query, resolved_format)
