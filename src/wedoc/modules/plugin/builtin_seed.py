"""Boot-time seed for the official built-in plugins (Chart + Sheet Form).

Ports ``OfficialPluginInitService.onModuleInit``: idempotently upsert the two
official plugin rows (fixed ids, ``createdBy='system'``, ``status='published'``)
so ``GET /api/plugin/center/list`` matches a stock upstream deployment.

The logo is recorded as the storage read-path only (``/plugin/{id}``) — the
binary asset is not shipped, which still reproduces upstream's stored ``logo``
value byte-for-byte (upstream stores ``/${dir}/${id}`` regardless of upload).
Brand-bearing literals carry a ``{b}`` token and are assembled through
``compat.render_builtin_brand`` at import time.
"""

import json
import os
from datetime import UTC, datetime

import bcrypt
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ...compat import render_builtin_brand as _sub
from ...config import Settings, get_settings
from ...db import engine as db_engine
from ...db.models_meta import Plugin

_SYSTEM = "system"

_CHART_DETAIL_DESC = _sub(
    "\n  If you're looking for a colorful way to get a big-picture overview of a table, try a c"
    'hart app.\n  \n  \n  \n  The chart app summarizes a table of records and turns it into an '
    'interactive bar, line, pie. \n  \n\n  [Learn more](https://{b}.ai)\n\n  '
)
_CHART_HELP_URL = _sub('https://help.{b}.ai/en/basic/plugin/chart')
_CHART_I18N = _sub(
    '{"zh":{"name":"图表","helpUrl":"https://help.{b}.cn/zh/basic/plugin/chart","description":"'
    '通过柱状图、折线图、饼图可视化您的记录","detailDesc":"如果您想通过色彩丰富的方式从大局上了'
    '解表格，试试图表应用。\\n\\n图表应用汇总表格记录，并将其转换为交互式的柱状图、折线图、饼图'
    '。\\n\\n[了解更多](https://{b}.cn)"}}'
)
_SHEETFORM_DETAIL_DESC = _sub(
    'Create powerful and flexible forms using the familiar spread sheet interface. \n\nWith the'
    ' sheet Form Designer plugin, you can: \n\n- Design form templates in spread sheet. \n\n- S'
    'hare your forms easily. \n\n- Collect data directly into your multi-dimensional table. \n'
    '\nPerfect for surveys, data collection, and customized form needs. \n\n[Learn more](https:'
    '//help.{b}.ai/en/basic/plugin/sheet-form)'
)
_SHEETFORM_HELP_URL = _sub('https://help.{b}.ai/en/basic/plugin/sheet-form')
_SHEETFORM_I18N = _sub(
    '{"zh":{"name":"Sheet 表单","helpUrl":"https://help.{b}.cn/zh/basic/plugin/sheet-form","des'
    'cription":"使用表格设计表单，并将数据收集到您的多维表格中","detailDesc":"使用熟悉的表格界'
    '面创建强大而灵活的表单。\\n\\n使用表格表单插件，您可以： \\n\\n - 在表格中设计表单模板。 '
    '\\n\\n - 轻松分享您的表格表单。 \\n\\n - 将数据直接收集到您的多维表格中。 \\n\\n非常适合问'
    '卷调查、数据收集和自定义表单需求。\\n\\n[了解更多](https://{b}.cn)"}}'
)

_SHEETFORM_DESCRIPTION = (
    "Design forms with spread sheet, then collect data into your table by sheet form"
)

_BUILTIN_PLUGINS: list[dict[str, object]] = [
    {
        "id": "plgchart",
        "name": "Chart",
        "description": "Visualize your records on a bar, line, pie",
        "detail_desc": _CHART_DETAIL_DESC,
        "help_url": _CHART_HELP_URL,
        "url": "/plugin/chart",
        "positions": ["dashboard", "panel"],
        "i18n": _CHART_I18N,
        "secret_env": "PLUGIN_CHART_SECRET",
    },
    {
        "id": "plgsheetform",
        "name": "Sheet Form",
        "description": _SHEETFORM_DESCRIPTION,
        "detail_desc": _SHEETFORM_DETAIL_DESC,
        "help_url": _SHEETFORM_HELP_URL,
        "url": "/plugin/sheet-form-view",
        "positions": ["view"],
        "i18n": _SHEETFORM_I18N,
        "secret_env": "PLUGIN_SHEETFORMVIEW_SECRET",
    },
]

# columns re-asserted on every boot; created_by/created_time stay as first seeded
_UPDATE_COLUMNS = (
    "name",
    "description",
    "detail_desc",
    "logo",
    "help_url",
    "status",
    "positions",
    "url",
    "secret",
    "masked_secret",
    "i18n",
    "plugin_user",
    "last_modified_time",
)


def _secret_material(env_name: str, settings: Settings) -> tuple[str, str]:
    """generateSecret() port: env override or secretKey fallback → (hashed, masked)."""
    secret = os.environ.get(env_name) or settings.resolved_secret_key
    hashed = bcrypt.hashpw(secret.encode()[:72], bcrypt.gensalt(10)).decode()
    sensitive = max(len(secret) - 10, 0)
    masked = "*" * sensitive + secret[sensitive:]
    return hashed, masked


async def seed_builtin_plugins() -> None:
    """Upsert the official plugin rows. Idempotent; safe to run on every boot."""
    settings = get_settings()
    now = datetime.now(UTC).replace(tzinfo=None)
    async with db_engine.session() as session:
        for spec in _BUILTIN_PLUGINS:
            hashed, masked = _secret_material(str(spec["secret_env"]), settings)
            values: dict[str, object] = {
                "id": spec["id"],
                "name": spec["name"],
                "description": spec["description"],
                "detail_desc": spec["detail_desc"],
                "logo": f"/plugin/{spec['id']}",
                "help_url": spec["help_url"],
                "status": "published",
                "positions": json.dumps(spec["positions"], separators=(",", ":")),
                "url": spec["url"],
                "secret": hashed,
                "masked_secret": masked,
                "i18n": spec["i18n"],
                "plugin_user": None,
                "created_by": _SYSTEM,
                "created_time": now,
                "last_modified_time": now,
            }
            stmt = pg_insert(Plugin).values(**values)
            stmt = stmt.on_conflict_do_update(
                index_elements=[Plugin.id],
                set_={col: values[col] for col in _UPDATE_COLUMNS},
            )
            await session.execute(stmt)
        await session.commit()

