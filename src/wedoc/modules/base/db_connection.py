"""Base read-only DB connection — ports base/db-connection.service.ts.

Creates a schema-scoped read-only Postgres role for a base and returns its DSN.
For the single-PG deployment the data database is the meta database; the DSN
host/port come from PUBLIC_DATABASE_PROXY when set (as upstream), otherwise from
the meta connection so the returned credentials are directly usable.
"""

from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import text

from ...config import get_settings
from ...core.errors import ApiError, HttpErrorCode
from ...core.ids import random_string
from ...db import engine as db_engine


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _readonly_target() -> dict[str, Any]:
    settings = get_settings()
    meta = urlsplit(settings.sqlalchemy_dsn.replace("postgresql+asyncpg://", "postgresql://"))
    database = (meta.path or "").lstrip("/")
    proxy = settings.public_database_proxy
    if proxy:
        parsed = urlsplit(f"https://{proxy}")
        return {"host": parsed.hostname, "port": parsed.port or 5432, "db": database}
    return {"host": meta.hostname, "port": meta.port or 5432, "db": database}


def _url_from_dsn(dsn: dict[str, Any]) -> str:
    params = "&".join(f"{k}={v}" for k, v in dsn["params"].items())
    return (
        f"postgresql://{dsn['user']}:{dsn['pass']}@{dsn['host']}:{dsn['port']}/{dsn['db']}?{params}"
    )


class DbConnectionService:
    async def create(self, base_id: str) -> dict[str, Any] | None:
        settings = get_settings()
        role = f"read_only_role_{base_id}"
        password = random_string(21)
        target = _readonly_target()
        max_conn = settings.default_max_base_db_connections
        async with db_engine.session() as session:
            base = (
                await session.execute(
                    text("SELECT id FROM base WHERE id = :id AND deleted_time IS NULL")
                    .bindparams(id=base_id)
                )
            ).first()
            if base is None:
                raise ApiError(
                    "Only base owner can create db connection",
                    HttpErrorCode.RESTRICTED_RESOURCE,
                    {"localization": {"i18nKey": "httpErrors.dbConnection.onlyOwnerCanCreate"}},
                )
            await session.execute(
                text("UPDATE base SET schema_pass = :pw WHERE id = :id").bindparams(
                    pw=password, id=base_id
                )
            )
            role_q = _quote_ident(role)
            schema_q = _quote_ident(base_id)
            await session.execute(
                text(
                    f"CREATE ROLE {role_q} WITH LOGIN PASSWORD {_quote_literal(password)} "
                    "NOSUPERUSER NOINHERIT NOCREATEDB NOCREATEROLE NOREPLICATION "
                    f"CONNECTION LIMIT {int(max_conn)}"
                )
            )
            await session.execute(text(f"GRANT USAGE ON SCHEMA {schema_q} TO {role_q}"))
            await session.execute(
                text(f"GRANT SELECT ON ALL TABLES IN SCHEMA {schema_q} TO {role_q}")
            )
            await session.execute(
                text(
                    f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema_q} "
                    f"GRANT SELECT ON TABLES TO {role_q}"
                )
            )
            await session.commit()
        dsn = {
            "driver": "postgresql",
            "host": target["host"],
            "port": target["port"],
            "db": target["db"],
            "user": role,
            "pass": password,
            "params": {"schema": base_id},
        }
        return {
            "dsn": dsn,
            "connection": {"max": max_conn, "current": 0},
            "url": _url_from_dsn(dsn),
        }

    async def retrieve(self, base_id: str) -> dict[str, Any] | None:
        settings = get_settings()
        role = f"read_only_role_{base_id}"
        async with db_engine.session() as session:
            base = (
                await session.execute(
                    text("SELECT schema_pass FROM base WHERE id = :id AND deleted_time IS NULL")
                    .bindparams(id=base_id)
                )
            ).first()
            if base is None or not base[0]:
                return None
            exists = (
                await session.execute(
                    text("SELECT count(*) FROM pg_roles WHERE rolname = :r").bindparams(r=role)
                )
            ).scalar()
            if not exists:
                raise ApiError(
                    "Role does not exist",
                    HttpErrorCode.INTERNAL_SERVER_ERROR,
                    {"localization": {"i18nKey": "httpErrors.dbConnection.roleNotExist"}},
                )
            current = (
                await session.execute(
                    text("SELECT COUNT(*) FROM pg_stat_activity WHERE usename = :r").bindparams(
                        r=role
                    )
                )
            ).scalar() or 0
            schema_pass = base[0]
        target = _readonly_target()
        dsn = {
            "driver": "postgresql",
            "host": target["host"],
            "port": target["port"],
            "db": target["db"],
            "user": role,
            "pass": schema_pass,
            "params": {"schema": base_id},
        }
        return {
            "dsn": dsn,
            "connection": {
                "max": settings.default_max_base_db_connections,
                "current": int(current),
            },
            "url": _url_from_dsn(dsn),
        }

    async def remove(self, base_id: str) -> None:
        role = f"read_only_role_{base_id}"
        async with db_engine.session() as session:
            base = (
                await session.execute(
                    text("SELECT id FROM base WHERE id = :id AND deleted_time IS NULL")
                    .bindparams(id=base_id)
                )
            ).first()
            if base is None:
                raise ApiError(
                    "Only the base owner can remove a db connection",
                    HttpErrorCode.RESTRICTED_RESOURCE,
                    {"localization": {"i18nKey": "httpErrors.dbConnection.onlyOwnerCanRemove"}},
                )
            role_q = _quote_ident(role)
            schema_q = _quote_ident(base_id)
            await session.execute(text(f"REVOKE USAGE ON SCHEMA {schema_q} FROM {role_q}"))
            await session.execute(
                text(
                    f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema_q} "
                    f"REVOKE ALL ON TABLES FROM {role_q}"
                )
            )
            await session.execute(
                text(f"REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA {schema_q} FROM {role_q}")
            )
            await session.execute(text(f"DROP ROLE IF EXISTS {role_q}"))
            await session.execute(
                text("UPDATE base SET schema_pass = NULL WHERE id = :id").bindparams(id=base_id)
            )
            await session.commit()
