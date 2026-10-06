"""base domain service — ports features/base/base.service.ts.

Link invitations, collaborator management and the shared-base listing live in
their own services; this service owns the base row lifecycle plus the
permission/role views. Import/duplicate/template/export/connection/move/erd/
publish routes are not registered yet (see docs/api-parity-ledger.md).
"""

import json
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import delete, func, select, text

from ...core import cls
from ...core.errors import ApiError, HttpErrorCode
from ...core.ids import IdPrefix, new_id, random_string
from ...core.security.permissions import Role, get_max_level_role
from ...core.storage import get_public_full_storage_url
from ...db import engine as db_engine
from ...db.models_meta import (
    BaseShare,
    Collaborator,
    Invitation,
    InvitationRecord,
    Space,
    Template,
    Trash,
)
from ...db.provider import create_schema_sql, drop_schema_sql
from ..collaborator.service import RESOURCE_BASE, RESOURCE_SPACE, CollaboratorService
from ..space import repository as space_repository
from . import repository

logger = structlog.get_logger(__name__)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


# getPermission() key set: Table + Base + Automation + App + TableRecordHistory
# prefixes (packages/core action order).
_PERMISSION_ACTIONS: tuple[str, ...] = (
    "table|create",
    "table|delete",
    "table|read",
    "table|update",
    "table|import",
    "table|export",
    "table|trash_read",
    "table|trash_update",
    "table|trash_reset",
    "table|archive_read",
    "table|archive_manage",
    "base|create",
    "base|delete",
    "base|read",
    "base|read_all",
    "base|update",
    "base|invite_email",
    "base|invite_link",
    "base|table_import",
    "base|table_export",
    "base|authority_matrix_config",
    "base|db_connection",
    "base|query_data",
    "automation|create",
    "automation|delete",
    "automation|read",
    "automation|update",
    "routine|create",
    "routine|delete",
    "routine|read",
    "routine|update",
    "app|create",
    "app|delete",
    "app|read",
    "app|update",
    "table_record_history|read",
)


def _not_found() -> ApiError:
    return ApiError(
        "Project not found",
        HttpErrorCode.NOT_FOUND,
        {"localization": {"i18nKey": "httpErrors.base.notFound"}},
    )


# fields held back from duplication's scalar pass and re-provisioned later so
# their references remap into the new base: link fields in pass 2, lookup/rollup/
# formula in pass 4. conditionalRollup / conditional lookups have no create path
# in wedoc (a source base can't contain one) and stay dropped.
_DEFERRED_COMPUTED_TYPES = {"formula", "rollup", "conditionalRollup"}


def _is_link_or_computed(field: dict[str, Any]) -> bool:
    return (
        field["type"] == "link"
        or field["type"] in _DEFERRED_COMPUTED_TYPES
        or bool(field.get("isLookup"))
        or bool(field.get("isConditionalLookup"))
    )


def _remap_formula_expression(expression: str, field_map: dict[str, str]) -> str:
    """Rewrite ``{fldXxx}`` id tokens via field_map, skipping ids inside string
    literals and comments (mirrors the formula lexer's scanning)."""
    out: list[str] = []
    i, n = 0, len(expression)
    while i < n:
        ch = expression[i]
        if ch == "/" and i + 1 < n and expression[i + 1] == "/":
            j = i + 2
            while j < n and expression[j] not in "\r\n":
                j += 1
            out.append(expression[i:j])
            i = j
        elif ch == "/" and i + 1 < n and expression[i + 1] == "*":
            end = expression.find("*/", i + 2)
            end = n if end == -1 else end + 2
            out.append(expression[i:end])
            i = end
        elif ch in "'\"":
            j = i + 1
            while j < n:
                if expression[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                if expression[j] == ch:
                    j += 1
                    break
                j += 1
            out.append(expression[i:j])
            i = j
        elif ch == "{":
            end = expression.find("}", i + 1)
            if end == -1:
                out.append(expression[i:])
                break
            inner = expression[i + 1 : end].strip()
            out.append("{" + field_map.get(inner, inner) + "}")
            i = end + 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


class BaseService:
    def __init__(self) -> None:
        self.collaborators = CollaboratorService()

    async def create_base(
        self, space_id: str, name: str | None, icon: str | None
    ) -> dict[str, Any]:
        user_id = cls.get("user.id")
        space = await space_repository.get_space_row(space_id)
        if space is None:
            raise ApiError(
                "Space not found",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.space.notFound"}},
            )
        order = await repository.get_max_order(space_id) + 1
        base_id = new_id(IdPrefix.BASE)
        now = datetime.now(UTC).replace(tzinfo=None)
        row = await repository.insert_base(
            {
                "id": base_id,
                "name": name or "Untitled Base",
                "space_id": space_id,
                "order": order,
                "icon": icon,
                "v2_enabled": True,
                "created_by": user_id,
                "created_time": now,
                "last_modified_by": user_id,
                "provision_state": "pending",
            }
        )
        try:
            async with db_engine.session() as session:
                for sql in create_schema_sql(base_id):
                    await session.execute(text(sql))
                await session.commit()
            await repository.update_base_row(
                base_id, {"provision_state": "ready", "last_modified_by": user_id}
            )
        except Exception:
            await repository.update_base_row(
                base_id, {"provision_state": "error", "last_modified_by": user_id}
            )
            raise
        await self._mark_base_visited(base_id, space_id)
        return {
            "id": row["id"],
            "name": row["name"],
            "icon": row["icon"],
            "spaceId": row["space_id"],
        }

    async def _mark_base_visited(self, base_id: str, space_id: str) -> None:
        user_id = cls.get("user.id")
        if not user_id:
            return
        try:
            now = datetime.now(UTC).replace(tzinfo=None)
            await repository.mark_base_visited(
                random_string(16), user_id, base_id, space_id, now
            )
        except Exception as exc:  # ref only warns here
            logger.warn("failed to seed last-visit for base", base_id=base_id, error=str(exc))

    async def get_base_by_id(self, base_id: str) -> dict[str, Any]:
        base = await repository.get_base_row(base_id)
        if base is None:
            raise _not_found()
        template = cls.get("template")
        base_share = cls.get("baseShare")
        if template or base_share:
            role: str = Role.VIEWER
            collaborator_type = RESOURCE_BASE
        else:
            role, collaborator_type = await self._role_by_base_id(base_id, base["space_id"])
        result: dict[str, Any] = {
            "id": base["id"],
            "name": base["name"],
            "icon": base["icon"],
            "spaceId": base["space_id"],
            "createdBy": base["created_by"],
            "role": role,
            "collaboratorType": collaborator_type,
            "enabledAuthority": False,
            "restrictedAuthority": False,
        }
        if template and template.get("baseId") == base_id:
            from ...core.security.permissions import PermissionService

            result["template"] = {
                "id": template["id"],
                "headers": PermissionService().generate_template_header(template["id"]),
            }
        if base.get("v2_enabled"):
            result["v2Status"] = {"useV2": True, "reason": "new_base"}
        return result

    async def _role_by_base_id(self, base_id: str, space_id: str) -> tuple[str, str]:
        user_id = cls.get("user.id")
        rows = await space_repository.list_collaborators_by_principals_and_resources(
            [user_id], [base_id, space_id]
        )
        if not rows:
            raise ApiError(
                "Cannot access base",
                HttpErrorCode.RESTRICTED_RESOURCE,
                {
                    "localization": {
                        "i18nKey": "httpErrors.base.cannotAccess",
                        "context": {"baseId": base_id},
                    }
                },
            )
        role = get_max_level_role(rows)
        # On equal roles prefer the space row (space features gate on it).
        collaborator = next(
            (r for r in rows if r["role_name"] == role and r["resource_type"] == RESOURCE_SPACE),
            None,
        ) or next((r for r in rows if r["role_name"] == role), None)
        return role, collaborator["resource_type"] if collaborator else RESOURCE_SPACE

    async def update_base(
        self, base_id: str, name: str | None, icon: str | None, icon_set: bool
    ) -> dict[str, Any]:
        fields: dict[str, Any] = {"last_modified_by": cls.get("user.id")}
        if name is not None:
            fields["name"] = name
        if icon_set:
            fields["icon"] = icon
        row = await repository.update_base_row(base_id, fields)
        if row is None or row["deleted_time"] is not None:
            raise _not_found()
        return {
            "id": row["id"],
            "name": row["name"],
            "spaceId": row["space_id"],
            "icon": row["icon"],
        }

    async def move_base(self, base_id: str, target_space_id: str) -> dict[str, Any]:
        base = await repository.get_base_row(base_id)
        if base is None or base["deleted_time"] is not None:
            raise _not_found()
        space = await space_repository.get_space_row(target_space_id)
        if space is None:
            raise ApiError(
                "Space not found",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.space.notFound"}},
            )
        from ...core.security.permissions import PermissionService

        await PermissionService().valid_permissions(
            target_space_id, ["base|create"], cls.get("accessTokenId")
        )
        # Single-PG deployment: no cross-data-DB physical move, and wedoc has no
        # link fields yet, so there are no cross-space link conversions — a
        # meta-only ownership transfer (base.spaceId update) suffices.
        await repository.update_base_row(base_id, {"space_id": target_space_id})
        return {}

    @staticmethod
    def _data_db_endpoint() -> dict[str, Any]:
        # Single shared PG: both spaces resolve to the same meta-fallback
        # endpoint (public schema), mirroring the reference dev deployment.
        return {"mode": "default", "cacheKey": "meta-fallback", "internalSchema": "public"}

    def _data_db_check(self) -> dict[str, Any]:
        endpoint = self._data_db_endpoint()
        return {
            "sameDataDb": True,
            "requiresPhysicalMove": False,
            "source": endpoint,
            "target": dict(endpoint),
        }

    async def check_move_base(
        self, base_id: str, target_space_id: str | None
    ) -> dict[str, Any]:
        base = await repository.get_base_row(base_id)
        if base is None or base["deleted_time"] is not None:
            raise _not_found()
        # The reference's query schema requires spaceId; when it is absent the
        # controller passes undefined downstream and throws an unhandled 500.
        if not target_space_id:
            raise ApiError("Internal Server Error", HttpErrorCode.INTERNAL_SERVER_ERROR)
        # No cross-space link fields in wedoc → no affected fields; the single
        # shared PG always resolves to the same (meta-fallback) data database.
        return {"affectedFields": [], "dataDb": self._data_db_check()}

    async def get_move_job(self, base_id: str, job_id: str) -> dict[str, Any]:
        # No physical cross-data-DB move ever runs on the single shared PG, so
        # any job id is unknown.
        raise ApiError(
            f"Move job {job_id} not found",
            HttpErrorCode.NOT_FOUND,
        )

    async def duplicate_base_check(
        self, base_id: str, dest_space_id: str | None
    ) -> dict[str, Any]:
        base = await repository.get_base_row(base_id)
        if base is None or base["deleted_time"] is not None:
            raise _not_found()
        return {"affectedFields": []}

    async def create_base_from_template(
        self, space_id: str, template_id: str, with_records: bool, base_id: str | None
    ) -> dict[str, Any]:
        from ..template import repository as template_repository

        # ref createBaseFromTemplate: findUniqueOrThrow(template) → 500 for an unknown
        # id; an existing template whose snapshot has no baseId → 404. The template's
        # snapshot.baseId is the source base that gets duplicated into the space.
        template = await template_repository.get_template_or_500(template_id)
        if base_id is not None:
            target = await repository.get_base_row(base_id)
            if target is None or target["deleted_time"] is not None:
                raise _not_found()
            if target["space_id"] != space_id:
                raise ApiError(
                    "BaseId and spaceId mismatch",
                    HttpErrorCode.VALIDATION_ERROR,
                    {
                        "localization": {
                            "i18nKey": "httpErrors.base.baseAndSpaceMismatch",
                            "context": {"baseId": base_id, "spaceId": space_id},
                        }
                    },
                )
        snapshot_raw = template.get("snapshot")
        from_base_id = ""
        if snapshot_raw:
            try:
                from_base_id = (json.loads(snapshot_raw) or {}).get("baseId") or ""
            except (TypeError, ValueError):
                from_base_id = ""
        if not from_base_id:
            raise ApiError(
                "Template not found",
                HttpErrorCode.NOT_FOUND,
                {
                    "localization": {
                        "i18nKey": "httpErrors.base.templateNotFound",
                        "context": {"templateId": template_id},
                    }
                },
            )
        # base_id None → provision a fresh base; base_id set → ADD the template's
        # tables into that existing base (apply-into-existing). Both bump usageCount.
        result = await self.duplicate_base_impl(
            from_base_id, space_id, with_records, template.get("name"), target_base_id=base_id
        )
        new_count = (template.get("usageCount") or 0) + 1
        await template_repository.update_template_row(template_id, {"usage_count": new_count})
        return result

    async def duplicate_base(
        self,
        from_base_id: str,
        space_id: str,
        with_records: bool,
        name: str | None,
    ) -> dict[str, Any]:
        from ...core.security.permissions import PermissionService

        source = await repository.get_base_row(from_base_id)
        if source is None or source["deleted_time"] is not None:
            # ref duplicateBase: findUniqueOrThrow surfaces a bare
            # "Project {id} not found" (no localization data).
            raise ApiError(f"Project {from_base_id} not found", HttpErrorCode.NOT_FOUND)
        # ref: base|update on the source. The share-copy path skips this (share
        # grants access) and calls duplicate_base_impl directly.
        await PermissionService().valid_permissions(
            from_base_id, ["base|update"], cls.get("accessTokenId")
        )
        return await self.duplicate_base_impl(from_base_id, space_id, with_records, name)

    async def duplicate_base_impl(
        self,
        from_base_id: str,
        space_id: str,
        with_records: bool,
        name: str | None,
        target_base_id: str | None = None,
    ) -> dict[str, Any]:
        from types import SimpleNamespace

        from ...formula import FormulaError, reference_field_ids
        from ...formula import parse as parse_formula
        from ..field import link_field
        from ..field.schemas import FieldCreateBody, FieldPatchBody
        from ..field.service import FieldService
        from ..record.link_cells import link_input_ids
        from ..record.schemas import RecordBulkPatchBody
        from ..record.service import RecordService
        from ..table import repository as table_repository
        from ..table.service import TableService

        source = await repository.get_base_row(from_base_id)
        if source is None or source["deleted_time"] is not None:
            raise _not_found()

        if target_base_id is not None:
            # apply-into-existing: reuse the pre-existing target base and ADD the
            # template's tables alongside whatever it already holds (v2 allows
            # duplicate table names — no dedup, no clear). The reference's exact v2
            # merge/clear semantics live in a large baseDuplicate service and can't be
            # oracle-verified here, so ADD is a documented approximation.
            target = await repository.get_base_row(target_base_id)
            if target is None or target["deleted_time"] is not None:
                raise _not_found()
            new_base_id = target_base_id
            result_name, result_space_id, result_icon = (
                target["name"],
                target["space_id"],
                target.get("icon"),
            )
        else:
            new_name = name or f"{source['name']} (Copy)"
            new_base = await self.create_base(space_id, new_name, source.get("icon"))
            new_base_id = new_base["id"]
            result_name, result_space_id, result_icon = new_name, space_id, source.get("icon")

        table_service = TableService()
        field_service = FieldService()
        record_service = RecordService()

        table_map: dict[str, str] = {}
        field_map: dict[str, str] = {}
        record_map: dict[str, str] = {}
        src_field_by_id: dict[str, dict[str, Any]] = {}
        plans: list[dict[str, Any]] = []
        # pass 1: recreate each table with its scalar/system fields, views and
        # records. Link/computed fields are held back so every target table exists
        # before links are provisioned; columnMeta is regenerated against the new
        # field ids (custom widths/hidden flags are not preserved).
        tables = await table_service.list_tables(from_base_id)
        for table in tables:
            fields = await field_service.list_fields(table["id"])
            for f in fields:
                src_field_by_id[f["id"]] = f
            excluded = {f["name"] for f in fields if _is_link_or_computed(f)}
            field_ros = [
                {
                    "name": f["name"],
                    "type": f["type"],
                    "dbFieldName": f["dbFieldName"],
                    "options": f.get("options"),
                    "isPrimary": f.get("isPrimary") or None,
                    "notNull": f.get("notNull") or None,
                    "unique": f.get("unique") or False,
                }
                for f in fields
                if not _is_link_or_computed(f)
            ]
            view_rows = await table_repository.list_view_rows(table["id"])
            view_ros = [{"name": v["name"], "type": v["type"]} for v in view_rows]
            src_records: list[dict[str, Any]] = []
            record_ros: list[dict[str, Any]] = []
            if with_records:
                data = await record_service.list_records(
                    table["id"], field_key_type="name", take=100000
                )
                src_records = data["records"]
                record_ros = [
                    {"fields": {k: v for k, v in r["fields"].items() if k not in excluded}}
                    for r in src_records
                ]
            body = SimpleNamespace(
                name=table["name"], fields=field_ros, views=view_ros,
                records=record_ros, fieldKeyType=None, dbTableName=None,
            )
            created = await table_service.create_table(new_base_id, body)
            table_map[table["id"]] = created["id"]
            new_by_name = {nf["name"]: nf["id"] for nf in created["fields"]}
            for f in fields:
                if not _is_link_or_computed(f) and f["name"] in new_by_name:
                    field_map[f["id"]] = new_by_name[f["name"]]
            if with_records:
                for src_r, new_r in zip(src_records, created["records"], strict=True):
                    record_map[src_r["id"]] = new_r["id"]
            plans.append(
                {
                    "src_fields": fields,
                    "new_table_id": created["id"],
                    "src_records": src_records,
                }
            )
        # pass 2: provision link fields through the field service so each gains its
        # symmetric field, FK columns and junction tables in the new base. The
        # foreign table id is remapped; a two-way link auto-creates the reverse
        # side, so the pair is created once — the source's symmetric side is skipped
        # and mapped to the generated reverse field, renamed to the source name.
        handled_symmetric: set[str] = set()
        for plan in plans:
            for f in plan["src_fields"]:
                if f["type"] != "link" or f.get("isLookup"):
                    continue
                if f["id"] in handled_symmetric:
                    continue
                options = f.get("options") or {}
                new_foreign_id = table_map.get(options.get("foreignTableId"))
                if new_foreign_id is None:
                    continue
                new_options: dict[str, Any] = {
                    "relationship": options.get("relationship"),
                    "foreignTableId": new_foreign_id,
                    "isOneWay": bool(options.get("isOneWay")),
                }
                new_lookup = field_map.get(options.get("lookupFieldId"))
                if new_lookup is not None:
                    new_options["lookupFieldId"] = new_lookup
                new_vo = await field_service.create_field(
                    plan["new_table_id"],
                    FieldCreateBody.zod_validate(
                        {"type": "link", "name": f["name"], "options": new_options}
                    ),
                )
                field_map[f["id"]] = new_vo["id"]
                plan.setdefault("main_links", []).append((f, new_vo["id"]))
                src_sym_id = options.get("symmetricFieldId")
                new_sym_id = (new_vo.get("options") or {}).get("symmetricFieldId")
                if src_sym_id and new_sym_id:
                    handled_symmetric.add(src_sym_id)
                    field_map[src_sym_id] = new_sym_id
                    sym_src = src_field_by_id.get(src_sym_id)
                    if sym_src is not None:
                        await field_service.update_field(
                            new_foreign_id,
                            new_sym_id,
                            FieldPatchBody.zod_validate({"name": sym_src["name"]}),
                        )
        # pass 3: rewrite link cells on the copied records, remapping each foreign
        # record id through record_map (dropping refs with no copy). Only each
        # link's main side is written; its symmetric cells are recomputed from it.
        if with_records:
            for plan in plans:
                main_links = plan.get("main_links")
                if not main_links:
                    continue
                patches: dict[str, dict[str, Any]] = {}
                for src_field, new_field_id in main_links:
                    options = src_field.get("options") or {}
                    multi = link_field.is_multi_value_link(options.get("relationship"))
                    name_key = src_field["name"]
                    for src_record in plan["src_records"]:
                        new_rid = record_map.get(src_record["id"])
                        if new_rid is None:
                            continue
                        mapped = [
                            record_map[i]
                            for i in link_input_ids(src_record["fields"].get(name_key))
                            if i in record_map
                        ]
                        if not mapped:
                            continue
                        value = [{"id": i} for i in mapped] if multi else {"id": mapped[0]}
                        patches.setdefault(new_rid, {})[new_field_id] = value
                if not patches:
                    continue
                await record_service.update_records(
                    plan["new_table_id"],
                    list(patches.keys()),
                    RecordBulkPatchBody.zod_validate(
                        {
                            "fieldKeyType": "id",
                            "records": [
                                {"id": rid, "fields": flds} for rid, flds in patches.items()
                            ],
                        }
                    ),
                )

        # pass 4: recreate lookup/rollup/formula fields with references remapped.
        # Cells resolve at read time (no backfill); a dependency graph over field
        # refs orders creation (rollup/lookup may target computed foreign fields, a
        # formula may reference lookups/rollups/other formulas). Cycles or
        # unresolved refs are skipped, never failing the whole duplication.
        computed: dict[str, tuple[str, dict[str, Any], str, set[str] | None]] = {}
        for plan in plans:
            for f in plan["src_fields"]:
                if f.get("isConditionalLookup") or f["type"] == "conditionalRollup":
                    continue
                if f.get("isLookup"):
                    kind = "lookup"
                elif f["type"] == "rollup":
                    kind = "rollup"
                elif f["type"] == "formula":
                    kind = "formula"
                else:
                    continue
                if kind == "formula":
                    expr = (f.get("options") or {}).get("expression") or ""
                    try:
                        refs: set[str] | None = set(reference_field_ids(parse_formula(expr)))
                    except FormulaError:
                        refs = None
                else:
                    lo = f.get("lookupOptions") or {}
                    refs = {r for r in (lo.get("linkFieldId"), lo.get("lookupFieldId")) if r}
                computed[f["id"]] = (plan["new_table_id"], f, kind, refs)
        pending = list(computed)
        while pending:
            ready = [
                fid
                for fid in pending
                if computed[fid][3] is not None
                and all(r in field_map for r in computed[fid][3])
            ]
            if not ready:
                break
            for fid in ready:
                new_table_id, f, kind, _refs = computed[fid]
                try:
                    new_computed_id = await self._duplicate_computed_field(
                        field_service, new_table_id, f, kind, field_map, table_map
                    )
                except Exception as exc:  # unresolved ref / unsupported shape: skip
                    logger.warn(
                        "skip computed field in base copy", field=f.get("id"), error=str(exc)
                    )
                    new_computed_id = None
                if new_computed_id is not None:
                    field_map[fid] = new_computed_id
            ready_set = set(ready)
            pending = [fid for fid in pending if fid not in ready_set]

        return {
            "id": new_base_id,
            "name": result_name,
            "spaceId": result_space_id,
            "icon": result_icon,
        }

    async def _duplicate_computed_field(
        self,
        field_service: Any,
        table_id: str,
        f: dict[str, Any],
        kind: str,
        field_map: dict[str, str],
        table_map: dict[str, str],
    ) -> str | None:
        """Create one copied lookup/rollup/formula field with refs remapped; return
        its new id, or None when a reference cannot be remapped."""
        from ..field.schemas import FieldCreateBody

        if kind == "formula":
            expr = _remap_formula_expression(
                (f.get("options") or {}).get("expression") or "", field_map
            )
            body: dict[str, Any] = {
                "type": "formula",
                "name": f["name"],
                "options": {**(f.get("options") or {}), "expression": expr},
            }
        else:
            lo = f.get("lookupOptions") or {}
            new_link = field_map.get(lo.get("linkFieldId"))
            new_foreign = table_map.get(lo.get("foreignTableId"))
            new_lookup = field_map.get(lo.get("lookupFieldId"))
            if not (new_link and new_foreign and new_lookup):
                return None
            body = {
                "name": f["name"],
                "options": f.get("options") or {},
                "lookupOptions": {
                    "linkFieldId": new_link,
                    "foreignTableId": new_foreign,
                    "lookupFieldId": new_lookup,
                },
            }
            if kind == "lookup":
                body["type"] = f["type"]
                body["isLookup"] = True
            else:
                body["type"] = "rollup"
        vo = await field_service.create_field(table_id, FieldCreateBody.zod_validate(body))
        return vo["id"]

    async def update_order(self, base_id: str, anchor_id: str, position: str) -> None:
        base = await repository.get_base_row(base_id)
        if base is None:
            raise _not_found()
        anchor = await self._get_anchor(base["space_id"], anchor_id)
        new_order = await self._compute_order(base["space_id"], anchor, position)
        await repository.update_base_row(base_id, {"order": new_order})

    async def _get_anchor(self, space_id: str, anchor_id: str) -> dict[str, Any]:
        anchor = await repository.get_base_row(anchor_id)
        if anchor is None or anchor["space_id"] != space_id:
            raise ApiError(
                "Anchor base not found",
                HttpErrorCode.NOT_FOUND,
                {
                    "localization": {
                        "i18nKey": "httpErrors.base.anchorNotFound",
                        "context": {"anchorId": anchor_id},
                    }
                },
            )
        return anchor

    async def _compute_order(
        self, space_id: str, anchor: dict[str, Any], position: str
    ) -> float:
        below = position == "after"
        neighbor = await repository.list_next_base_by_order(
            space_id, anchor["order"], below=position == "before"
        )
        if neighbor is None:
            return anchor["order"] + (1 if below else -1)
        order = (neighbor["order"] + anchor["order"]) / 2
        if abs(order - anchor["order"]) < 2 * 2.220446049250313e-16:
            # gap exhausted: re-shuffle the space to integral orders, recompute.
            await self._shuffle_orders(space_id)
            anchor = await self._get_anchor(space_id, anchor["id"])
            return await self._compute_order(space_id, anchor, position)
        return order

    async def _shuffle_orders(self, space_id: str) -> None:
        bases = await space_repository.list_base_rows_by_space(space_id)
        for index, base in enumerate(bases, start=1):
            await repository.update_base_row(base["id"], {"order": float(index)})

    async def delete_base(self, base_id: str) -> None:
        base = await repository.get_base_row(base_id)
        if base is None:
            raise _not_found()
        deleted_time = datetime.now(UTC).replace(tzinfo=None)
        await repository.soft_delete_base_row(base_id, deleted_time)
        from ..trash.listener import record_resource_deleted

        await record_resource_deleted(
            "base", base_id, base["space_id"], deleted_time
        )

    async def permanent_delete_base(self, base_id: str) -> None:
        from ...core.security.permissions import PermissionService

        await PermissionService().valid_permissions(
            base_id, ["base|delete"], cls.get("accessTokenId"), True
        )
        base = await repository.get_base_row(base_id, include_deleted=True)
        if base is None:
            raise _not_found()
        # drop physical schema, then clear all table meta rows (any state) so the
        # base row is no longer referenced by table_meta_base_id_fkey.
        from ...db.models_meta import Field, TableMeta, View

        async with db_engine.session() as session:
            await session.execute(text(drop_schema_sql(base_id)))
            table_ids = (
                await session.execute(
                    select(TableMeta.id).where(TableMeta.base_id == base_id)
                )
            ).scalars().all()
            if table_ids:
                await session.execute(delete(Field).where(Field.table_id.in_(table_ids)))
                await session.execute(delete(View).where(View.table_id.in_(table_ids)))
                await session.execute(
                    delete(TableMeta).where(TableMeta.base_id == base_id)
                )
            await session.commit()
        await self._clean_base_related_data(base_id)

    async def _clean_base_related_data(self, base_id: str) -> None:
        async with db_engine.session() as session:
            await session.execute(
                delete(Collaborator).where(
                    Collaborator.resource_id == base_id,
                    Collaborator.resource_type == RESOURCE_BASE,
                )
            )
            await session.execute(delete(Invitation).where(Invitation.base_id == base_id))
            await session.execute(
                delete(InvitationRecord).where(InvitationRecord.base_id == base_id)
            )
            await session.execute(delete(Trash).where(Trash.resource_id == base_id))
            await session.commit()
        await space_repository.delete_base_row(base_id)

    async def get_all_base_list(self) -> list[dict[str, Any]]:
        view = await self.collaborators.get_current_user_collaborators_base_and_space_array()
        bases = await repository.list_base_rows_by_space_any(
            view["spaceIds"], view["baseIds"]
        )
        if not bases:
            return []
        base_ids = [b["id"] for b in bases]
        shared_ids = await self._shared_base_ids(base_ids)
        users = await space_repository.list_user_rows_by_ids(
            list({b["created_by"] for b in bases})
        )
        user_map = {u["id"]: u for u in users}
        result = []
        for base in bases:
            created_user = user_map.get(base["created_by"])
            item: dict[str, Any] = {
                "id": base["id"],
                "name": base["name"],
                "order": base["order"],
                "spaceId": base["space_id"],
                "icon": base["icon"],
                "createdBy": base["created_by"],
                "lastModifiedTime": _iso(base["last_modified_time"]),
                "createdTime": _iso(base["created_time"]),
                "role": view["roleMap"].get(base["id"]) or view["roleMap"].get(base["space_id"]),
                "isShared": base["id"] in shared_ids,
            }
            if base.get("v2_enabled"):
                item["v2Status"] = {"useV2": True, "reason": "new_base"}
            if created_user:
                item["createdUser"] = {
                    "id": created_user["id"],
                    "name": created_user["name"],
                    "avatar": get_public_full_storage_url(created_user["avatar"])
                    if created_user["avatar"]
                    else None,
                }
            result.append(item)
        return result

    @staticmethod
    async def _shared_base_ids(base_ids: list[str]) -> set[str]:
        async with db_engine.session() as session:
            rows = (
                (
                    await session.execute(
                        select(BaseShare.base_id).where(
                            BaseShare.base_id.in_(base_ids),
                            BaseShare.node_id.is_(None),
                            BaseShare.enabled.is_(True),
                        )
                    )
                )
                .all()
            )
        return {r[0] for r in rows}

    async def get_permission(self) -> dict[str, bool]:
        granted = cls.get("permissions") or []
        return {action: action in granted for action in _PERMISSION_ACTIONS}

    async def generate_base_erd(self, base_id: str) -> dict[str, Any]:
        from ..field.service import FieldService
        from ..table.service import TableService

        # nodes are the base's tables (ordered) with their fields; edges derive
        # from link fields (none until link lands, so edges stay empty here).
        tables = await TableService().list_tables(base_id)
        field_service = FieldService()
        nodes: list[dict[str, Any]] = []
        for table in tables:
            fields = await field_service.list_fields(table["id"])
            items: list[dict[str, Any]] = []
            for f in fields:
                item = {"id": f["id"], "name": f["name"], "type": f["type"]}
                if f.get("isLookup"):
                    item["isLookup"] = True
                items.append(item)
            node: dict[str, Any] = {"id": table["id"], "name": table["name"], "fields": items}
            if table.get("icon") is not None:
                node["icon"] = table["icon"]
            nodes.append(node)
        return {"baseId": base_id, "nodes": nodes, "edges": []}

    async def _template_space_id(self) -> str:
        async with db_engine.session() as session:
            row = (
                await session.execute(
                    select(Space.id).where(
                        Space.is_template.is_(True), Space.deleted_time.is_(None)
                    )
                )
            ).first()
            if row is not None:
                return row[0]
            space_id = new_id(IdPrefix.SPACE)
            await session.execute(
                Space.__table__.insert().values(
                    id=space_id,
                    name="Templates",
                    is_template=True,
                    created_by=cls.get("user.id"),
                )
            )
            await session.commit()
        return space_id

    async def publish_base(self, base_id: str, body: Any) -> dict[str, Any]:
        source = await repository.get_base_row(base_id)
        if source is None or source["deleted_time"] is not None:
            raise _not_found()
        template_space_id = await self._template_space_id()
        include_data = body.includeData if body.includeData is not None else True
        snapshot = await self.duplicate_base_impl(
            base_id, template_space_id, include_data, source["name"]
        )
        snapshot_base_id = snapshot["id"]
        now = datetime.now(UTC).replace(tzinfo=None)
        snapshot_json = json.dumps(
            {
                "baseId": snapshot_base_id,
                "snapshotTime": _iso(now),
                "spaceId": template_space_id,
                "name": source["name"],
            },
            separators=(",", ":"),
        )
        publish_info = {
            "nodes": body.nodes,
            "includeData": body.includeData,
            "defaultActiveNodeId": body.defaultActiveNodeId,
            "snapshotActiveNodeId": None,
            "defaultUrl": None,
        }
        user_id = cls.get("user.id")
        async with db_engine.session() as session:
            existing = (
                await session.execute(
                    select(Template.id, Template.snapshot).where(Template.base_id == base_id)
                )
            ).first()
            if existing is not None:
                template_id = existing[0]
                old_snapshot = json.loads(existing[1]) if existing[1] else None
                await session.execute(
                    Template.__table__.update()
                    .where(Template.id == template_id)
                    .values(
                        name=body.title,
                        description=body.description,
                        snapshot=snapshot_json,
                        publish_info=publish_info,
                        last_modified_by=user_id,
                        last_modified_time=now,
                    )
                )
                await session.commit()
                if old_snapshot and old_snapshot.get("baseId"):
                    await self._drop_snapshot_base(old_snapshot["baseId"])
            else:
                template_id = new_id(IdPrefix.TEMPLATE)
                max_order = (
                    await session.execute(select(func.max(Template.order)))
                ).scalar()
                await session.execute(
                    Template.__table__.insert().values(
                        id=template_id,
                        base_id=base_id,
                        name=body.title,
                        description=body.description,
                        snapshot=snapshot_json,
                        publish_info=publish_info,
                        order=(max_order or 0) + 1,
                        created_by=user_id,
                        last_modified_time=now,
                    )
                )
                await session.commit()
        return {
            "baseId": snapshot_base_id,
            "defaultUrl": None,
            "permalink": f"/t/{template_id}",
        }

    async def _drop_snapshot_base(self, snapshot_base_id: str) -> None:
        # best-effort cleanup of a superseded snapshot base in the template space
        base = await repository.get_base_row(snapshot_base_id)
        if base is None:
            return
        try:
            await self.permanent_delete_base(snapshot_base_id)
        except Exception:
            pass
