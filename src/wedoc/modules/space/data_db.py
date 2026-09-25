"""BYODB space data-database preflight + summary — ports data-db-preflight.service.ts.

The deterministic paths (invalid URL, SSRF-blocked private host, admin-only
reject) match the reference byte-for-byte; a reachable external Postgres goes
through real capability detection + classification over asyncpg (positive parity
against a local docker PG). Fingerprints/internal-schema derivation reproduce the
upstream sha256 scheme; the brand-word schema prefix is assembled via compat.
"""

import hashlib
import ipaddress
import os
import socket
from typing import Any
from urllib.parse import unquote, urlsplit

from ... import compat
from ...core.errors import ApiError, HttpErrorCode
from ...db import engine as db_engine

_ADMIN_ONLY_MESSAGE = "Space data database configuration is only available from the admin panel"
_ADMIN_ONLY_CODE = "SPACE_DATA_DB_ADMIN_ONLY"
_MIGRATE_SPACE_TARGET_MODE = "migrate-space"

_DEFAULT_CAPABILITIES = {
    "createSchema": False,
    "createTable": False,
    "createFunction": False,
    "createTrigger": False,
    "createRole": False,
    "grantPrivileges": False,
    "inspectActivity": False,
}

_PRIVATE_NETWORK_ERROR = {
    "code": "PRIVATE_NETWORK_BLOCKED",
    "message": "Private network database hosts are blocked by default",
    "remediation": (
        f"Set {compat.upstream_env('SSRF_PROTECTION_DISABLED')}=true "
        "only in trusted self-hosted deployments."
    ),
}

_DATA_PLANE_TABLES = [
    "computed_update_outbox",
    "computed_update_outbox_seed",
    "computed_update_dead_letter",
    "computed_update_run_history",
    "computed_update_pause_scope",
    "computed_update_stage_ledger",
    "computed_field_activity",
    "computed_table_activity",
    "computed_task_field_ref",
    "record_history",
    "table_trash",
    "record_trash",
    "record_removal_tombstone",
    "__undo_log",
    "attachments",
    "attachments_table",
]
# Brand-bearing internal names are assembled at runtime (never inlined in source).
_DATA_SCHEMA_MIGRATION_TABLE = f"__{compat.upstream_brand()}_data_schema_migrations"
_DATA_PLANE_FUNCTIONS = [f"__{compat.upstream_brand()}_capture_undo_row"]
_ALLOWED_INTERNAL_TABLES = {*_DATA_PLANE_TABLES, _DATA_SCHEMA_MIGRATION_TABLE, "_prisma_migrations"}
_CLASSIFY_COMPATIBLE = f"{compat.upstream_brand()}-managed-compatible"
_CLASSIFY_INCOMPATIBLE = f"{compat.upstream_brand()}-managed-incompatible"


def admin_only_error() -> ApiError:
    return ApiError(
        _ADMIN_ONLY_MESSAGE,
        HttpErrorCode.RESTRICTED_RESOURCE,
        {"errorCode": _ADMIN_ONLY_CODE},
    )


def binding_not_found_error() -> ApiError:
    # getByodbBinding throws this when a space has no byodb binding; the single-PG
    # deployment never has one, so retest/retry always surface it.
    return ApiError("BYODB data database binding was not found", HttpErrorCode.NOT_FOUND)


def _ssrf_disabled() -> bool:
    return os.environ.get(compat.upstream_env("SSRF_PROTECTION_DISABLED")) == "true"


def _internal_schema_prefix() -> str:
    return (
        os.environ.get("BYODB_DATA_DB_INTERNAL_SCHEMA_PREFIX", "").strip()
        or compat.byodb_internal_schema_prefix()
    )


def _parse_dsn(url: str) -> dict[str, Any]:
    """parseDsn-equivalent (mirrors @httpx/dsn-parser + core parseDsn error text)."""
    parts = urlsplit(url)
    if "://" not in url or not parts.scheme or not parts.hostname:
        raise ValueError("DATABASE_URL PARSE_ERROR")
    driver = parts.scheme
    try:
        port = parts.port
    except ValueError as error:
        raise ValueError("DATABASE_URL PARSE_ERROR") from error
    if not port:
        raise ValueError("DATABASE_URL must provide a port")
    if driver not in ("postgres", "postgresql"):
        raise ValueError(f"DATABASE_URL driver {driver} is not supported")
    return {
        "host": parts.hostname,
        "port": port,
        "db": unquote(parts.path.lstrip("/")) if parts.path else "",
    }


def mask_database_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.password is None:
        return url
    netloc = ""
    if parts.username is not None:
        netloc += parts.username
    netloc += ":***"
    netloc += "@" + (parts.hostname or "")
    if parts.port is not None:
        netloc += f":{parts.port}"
    return parts._replace(netloc=netloc).geturl()


def _data_db_identity(url: str) -> str:
    parts = urlsplit(url)
    database = parts.path.lstrip("/")
    return f"{parts.hostname}:{parts.port}/{database}"


def resolve_internal_schema(internal_schema: str | None, url: str) -> str:
    resolved = (internal_schema or "").strip()
    if not resolved:
        digest = hashlib.sha256(_data_db_identity(url).encode()).hexdigest()[:16]
        resolved = f"{_internal_schema_prefix()}_{digest}"
    import re

    if not re.match(r"^[a-z_]\w*$", resolved, re.IGNORECASE):
        raise ValueError("Invalid data database internal schema name")
    return resolved


def fingerprint_connection(url: str, internal_schema: str) -> str:
    return "dbfp_" + hashlib.sha256(f"{url}\n{internal_schema}".encode()).hexdigest()


def fingerprint_url(url: str) -> str:
    return "dbfp_" + hashlib.sha256(url.encode()).hexdigest()


def _display_parts(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    host = f"{parts.hostname}:{parts.port}" if parts.port else (parts.hostname or "")
    database = unquote(parts.path.lstrip("/")) if parts.path else ""
    return host, database


def _is_blocked_address(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return not ip.is_global or ip.is_private or ip.is_loopback or ip.is_link_local


def _resolve_addresses(hostname: str) -> list[str]:
    try:
        ipaddress.ip_address(hostname)
        return [hostname]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(hostname, None)
    except OSError:
        return []
    return [info[4][0] for info in infos]


def _validate_network(url: str) -> dict[str, Any] | None:
    if _ssrf_disabled():
        return None
    hostname = urlsplit(url).hostname or ""
    addresses = _resolve_addresses(hostname)
    if any(_is_blocked_address(addr) for addr in addresses):
        return dict(_PRIVATE_NETWORK_ERROR)
    return None


def _build_result(
    *,
    errors: list[dict[str, Any]],
    classification: str,
    internal_schema: str = "",
    masked_url: str | None = None,
    url_fingerprint: str | None = None,
    display_host: str | None = None,
    display_database: str | None = None,
    server_version: str | None = None,
    available_databases: list[str] | None = None,
    requires_database_selection: bool | None = None,
    capabilities: dict[str, Any] | None = None,
) -> dict[str, Any]:
    usable = classification in ("empty", _CLASSIFY_COMPATIBLE)
    result: dict[str, Any] = {
        "ok": len(errors) == 0 and usable,
        "provider": "postgres",
        "maskedUrl": masked_url,
        "urlFingerprint": url_fingerprint,
        "displayHost": display_host,
        "displayDatabase": display_database,
        "internalSchema": internal_schema,
        "serverVersion": server_version,
        "classification": classification,
        "availableDatabases": available_databases,
        "requiresDatabaseSelection": requires_database_selection,
        "capabilities": capabilities or dict(_DEFAULT_CAPABILITIES),
        "errors": errors,
    }
    # zod .optional() fields drop when undefined; mirror by removing None values.
    # internalSchema is always passed by every call site (defaults to ""), so it
    # stays even when empty (matches the reference buildResult).
    keep = {"ok", "provider", "classification", "capabilities", "errors", "internalSchema"}
    return {k: v for k, v in result.items() if v is not None or k in keep}


class DataDbService:
    async def preflight(self, ro: Any) -> dict[str, Any]:
        url = ro.url
        errors: list[dict[str, Any]] = []
        try:
            _parse_dsn(url)
            internal_schema = resolve_internal_schema(ro.internalSchema, url)
            masked_url = mask_database_url(url)
            url_fingerprint = fingerprint_connection(url, internal_schema)
            display_host, display_database = _display_parts(url)
        except ValueError as error:
            return _build_result(
                errors=[
                    {
                        "code": "INVALID_DATABASE_URL",
                        "message": str(error).replace(url, "[redacted]") or "Invalid URL",
                    }
                ],
                classification="non-empty-unknown",
            )

        network_error = _validate_network(url)
        if network_error:
            return _build_result(
                errors=[network_error],
                masked_url=masked_url,
                url_fingerprint=url_fingerprint,
                display_host=display_host,
                display_database=display_database,
                classification="non-empty-unknown",
                internal_schema=internal_schema,
            )

        try:
            server_version, capabilities, classification = await self._inspect(
                url, internal_schema, errors
            )
        except Exception as error:
            errors.append(
                {
                    "code": "CONNECTION_FAILED",
                    "message": _sanitize(str(error), url),
                    "remediation": "Verify host, port, database name, credentials, and SSL "
                    "settings.",
                }
            )
            return _build_result(
                errors=errors,
                masked_url=masked_url,
                url_fingerprint=url_fingerprint,
                display_host=display_host,
                display_database=display_database,
                classification="non-empty-unknown",
                internal_schema=internal_schema,
            )
        return _build_result(
            errors=errors,
            masked_url=masked_url,
            url_fingerprint=url_fingerprint,
            display_host=display_host,
            display_database=display_database,
            server_version=server_version,
            capabilities=capabilities,
            classification=classification,
            internal_schema=internal_schema,
        )

    async def _inspect(
        self, url: str, internal_schema: str, errors: list[dict[str, Any]]
    ) -> tuple[str | None, dict[str, Any], str]:
        import asyncpg

        conn = await asyncpg.connect(dsn=url, timeout=15)
        try:
            server_version = await conn.fetchval("SHOW server_version")
            capabilities = await self._detect_capabilities(conn, errors)
            classification = await self._classify_target(conn, errors, internal_schema)
            return server_version, capabilities, classification
        finally:
            await conn.close()

    async def _detect_capabilities(
        self, conn: Any, errors: list[dict[str, Any]]
    ) -> dict[str, Any]:
        capabilities = dict(_DEFAULT_CAPABILITIES)
        read_only = await conn.fetchval("SHOW transaction_read_only")
        if str(read_only).lower() in ("on", "true", "1", "yes"):
            errors.append(
                {
                    "code": "READ_ONLY_DATABASE",
                    "message": "The target PostgreSQL connection is read-only and cannot be "
                    "used for BYODB.",
                    "remediation": "Use a writable primary database connection. For Supabase, "
                    "prefer the direct database endpoint or a writable session pooler instead "
                    "of a read-only replica endpoint.",
                }
            )
            return capabilities
        try:
            can_create = await conn.fetchval(
                "SELECT has_database_privilege(current_database(), 'CREATE')"
            )
            capabilities["grantPrivileges"] = bool(can_create)
        except Exception as error:
            errors.append({"code": "PRIVILEGE_CHECK_FAILED", "message": _sanitize(str(error), "")})

        import time as _time

        schema = f"__{compat.upstream_brand()}_byodb_preflight_{int(_time.time() * 1000)}"
        try:
            await conn.execute(f'CREATE SCHEMA "{schema}"')
            capabilities["createSchema"] = True
            await conn.execute(f'CREATE TABLE "{schema}"."check_parent" ("id" text PRIMARY KEY)')
            await conn.execute(
                f'CREATE TABLE "{schema}"."check_table" ("id" text PRIMARY KEY, "parent_id" text)'
            )
            await conn.execute(
                f'ALTER TABLE "{schema}"."check_table" ADD CONSTRAINT "check_table_parent_id_fkey" '
                f'FOREIGN KEY ("parent_id") REFERENCES "{schema}"."check_parent" ("id")'
            )
            capabilities["createTable"] = True
            await conn.execute(
                f'CREATE INDEX "check_table_id_idx" ON "{schema}"."check_table" ("id")'
            )
            await conn.execute(
                f'CREATE OR REPLACE FUNCTION "{schema}"."check_trigger_fn"() RETURNS trigger '
                "LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END; $$"
            )
            capabilities["createFunction"] = True
            await conn.execute(
                f'CREATE TRIGGER "check_trigger" BEFORE INSERT ON "{schema}"."check_table" '
                f'FOR EACH ROW EXECUTE FUNCTION "{schema}"."check_trigger_fn"()'
            )
            capabilities["createTrigger"] = True
        except Exception as error:
            errors.append(
                {
                    "code": "DDL_PRIVILEGE_CHECK_FAILED",
                    "message": _sanitize(str(error), ""),
                    "remediation": "Grant CREATE privileges required for schemas, tables, "
                    "functions, triggers, and foreign key constraints.",
                }
            )
        finally:
            try:
                await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            except Exception:
                pass
        try:
            can_create_role = await conn.fetchval(
                "SELECT rolsuper OR rolcreaterole FROM pg_roles WHERE rolname = current_user"
            )
            capabilities["createRole"] = bool(can_create_role)
        except Exception:
            capabilities["createRole"] = False
        try:
            await conn.execute("SELECT COUNT(*) FROM pg_stat_activity WHERE usename = current_user")
            capabilities["inspectActivity"] = True
        except Exception:
            capabilities["inspectActivity"] = False
        return capabilities

    async def _classify_target(
        self, conn: Any, errors: list[dict[str, Any]], internal_schema: str
    ) -> str:
        table_rows = await conn.fetch(
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_type = 'BASE TABLE' "
            "AND table_schema NOT IN ('information_schema','pg_catalog','pg_toast') "
            "AND table_schema NOT LIKE 'pg_%'"
        )
        function_rows = await conn.fetch(
            "SELECT routine_name FROM information_schema.routines WHERE routine_schema = $1",
            internal_schema,
        )
        internal_tables = [
            r["table_name"] for r in table_rows if r["table_schema"] == internal_schema
        ]
        functions = [r["routine_name"] for r in function_rows]
        unknown = [t for t in internal_tables if t not in _ALLOWED_INTERNAL_TABLES]
        managed_tables = [t for t in internal_tables if t in _DATA_PLANE_TABLES]
        managed_functions = [f for f in functions if f in _DATA_PLANE_FUNCTIONS]
        has_managed = bool(managed_tables) or bool(managed_functions)
        has_all_baseline = all(t in managed_tables for t in _DATA_PLANE_TABLES) and all(
            f in managed_functions for f in _DATA_PLANE_FUNCTIONS
        )
        if not has_managed and not unknown:
            return "empty"
        brand_cap = compat.upstream_brand().capitalize()
        if unknown:
            errors.append(
                {
                    "code": "NON_EMPTY_UNKNOWN_DATABASE",
                    "message": f"The {internal_schema} schema already contains objects outside "
                    f"{brand_cap} management",
                    "remediation": f"Use a database without a conflicting {internal_schema} "
                    "schema, or remove the unknown objects from that schema.",
                }
            )
            return "non-empty-unknown"
        if has_all_baseline:
            return _CLASSIFY_COMPATIBLE
        errors.append(
            {
                "code": f"INCOMPATIBLE_{compat.upstream_brand().upper()}_DATABASE",
                "message": f"The target database contains partial {brand_cap} data-plane objects",
                "remediation": f"Use a clean database or a compatible {brand_cap} data database "
                "manifest.",
            }
        )
        return _CLASSIFY_INCOMPATIBLE


def _sanitize(message: str, raw_url: str) -> str:
    import re

    result = message.replace(raw_url, "[redacted]") if raw_url else message
    return re.sub(r":[^:@/]+@", ":***@", result)


async def resolve_related_spaces(primary_space_id: str) -> dict[str, Any]:
    """Port of resolveSpaceDataDbRelatedSpaces for the single-PG deployment: no
    space carries a byodb binding, so every dataDb* field is null/default. The
    cross-space link BFS is preserved so multi-space link graphs still resolve."""
    import json as _json

    from sqlalchemy import select

    from ...db.models_meta import Base as BaseModel
    from ...db.models_meta import Field as FieldModel
    from ...db.models_meta import Space as SpaceModel
    from ...db.models_meta import TableMeta

    async with db_engine.session() as session:
        table_space = dict(
            (
                await session.execute(
                    select(TableMeta.id, BaseModel.space_id)
                    .join(BaseModel, BaseModel.id == TableMeta.base_id)
                    .where(TableMeta.deleted_time.is_(None), BaseModel.deleted_time.is_(None))
                )
            ).all()
        )
        field_rows = (
            await session.execute(
                select(
                    FieldModel.type,
                    FieldModel.is_lookup,
                    FieldModel.is_conditional_lookup,
                    FieldModel.options,
                    FieldModel.lookup_options,
                    BaseModel.space_id,
                )
                .join(TableMeta, TableMeta.id == FieldModel.table_id)
                .join(BaseModel, BaseModel.id == TableMeta.base_id)
                .where(FieldModel.deleted_time.is_(None), TableMeta.deleted_time.is_(None))
            )
        ).all()

    def _foreign_table_id(ftype, is_lookup, is_cond_lookup, options, lookup_options):
        is_link = ftype == "link" and not is_lookup
        is_cond_lu = bool(is_lookup and is_cond_lookup)
        is_cond_rollup = ftype == "conditionalRollup"
        if not (is_link or is_cond_lu or is_cond_rollup):
            return None
        blob = lookup_options if is_cond_lu else options
        if not blob:
            return None
        try:
            parsed = _json.loads(blob) if isinstance(blob, str) else blob
        except _json.JSONDecodeError:
            return None
        value = parsed.get("foreignTableId") if isinstance(parsed, dict) else None
        return value if isinstance(value, str) and value else None

    edges: list[dict[str, str]] = []
    for row in field_rows:
        foreign_table = _foreign_table_id(
            row.type, row.is_lookup, row.is_conditional_lookup, row.options, row.lookup_options
        )
        if not foreign_table:
            continue
        to_space = table_space.get(foreign_table)
        if to_space and to_space != row.space_id:
            edges.append({"from": row.space_id, "to": to_space})

    related = {primary_space_id}
    changed = True
    while changed:
        changed = False
        for edge in edges:
            if edge["from"] in related and edge["to"] not in related:
                related.add(edge["to"])
                changed = True
            elif edge["to"] in related and edge["from"] not in related:
                related.add(edge["from"])
                changed = True

    async with db_engine.session() as session:
        spaces = (
            await session.execute(
                select(SpaceModel.id, SpaceModel.name).where(
                    SpaceModel.id.in_(related), SpaceModel.deleted_time.is_(None)
                )
            )
        ).all()
        bases = (
            await session.execute(
                select(BaseModel.id, BaseModel.space_id).where(
                    BaseModel.space_id.in_(related), BaseModel.deleted_time.is_(None)
                )
            )
        ).all()
        tables = (
            await session.execute(
                select(TableMeta.id, BaseModel.space_id)
                .join(BaseModel, BaseModel.id == TableMeta.base_id)
                .where(BaseModel.space_id.in_(related), TableMeta.deleted_time.is_(None),
                       BaseModel.deleted_time.is_(None))
            )
        ).all()

    base_ids_by_space: dict[str, list[str]] = {}
    for base_id, space_id in bases:
        base_ids_by_space.setdefault(space_id, []).append(base_id)
    table_ids_by_space: dict[str, list[str]] = {}
    for table_id, space_id in tables:
        table_ids_by_space.setdefault(space_id, []).append(table_id)

    space_infos = [
        {
            "spaceId": space_id,
            "name": name,
            "isPrimary": space_id == primary_space_id,
            "baseIds": sorted(base_ids_by_space.get(space_id, [])),
            "tableIds": sorted(table_ids_by_space.get(space_id, [])),
            "dataDbMode": "default",
            "dataDbConnectionId": None,
            "dataDbUrlFingerprint": None,
            "dataDbDatabaseFingerprint": None,
            "dataDbDisplayHost": None,
            "dataDbDisplayDatabase": None,
            "dataDbInternalSchema": None,
        }
        for space_id, name in spaces
    ]
    space_infos.sort(
        key=lambda s: (not s["isPrimary"], s["name"], s["spaceId"])
    )
    component_ids = {s["spaceId"] for s in space_infos}
    resolved_links = sorted(
        (
            {
                "fromSpaceId": e["from"],
                "toSpaceId": e["to"],
            }
            for e in edges
            if e["from"] in component_ids and e["to"] in component_ids
        ),
        key=lambda link: (link["fromSpaceId"], link["toSpaceId"]),
    )
    return {
        "primarySpaceId": primary_space_id,
        "hasCrossSpaceLinks": len(space_infos) > 1,
        "spaces": space_infos,
        "links": resolved_links,
    }


class DataDbSummaryService:
    async def get_summary(
        self, space_id: str, include_related_spaces: bool = True
    ) -> dict[str, Any]:
        # Single-PG: no space_data_db_binding rows -> always default/ready.
        result: dict[str, Any] = {"mode": "default", "state": "ready"}
        if include_related_spaces:
            result["relatedSpaces"] = await resolve_related_spaces(space_id)
        return result
