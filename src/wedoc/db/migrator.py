"""Replays vendored upstream migration SQL with a Prisma-compatible ledger.

Keeps `_prisma_migrations` byte-compatible with what `prisma migrate deploy`
writes (id uuid, sha256 checksum of the restored script, started_at /
finished_at / applied_steps_count), so wedoc and the reference backend can
point at the same database interchangeably.
"""

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path

import asyncpg
import structlog

from ..compat import upstream_brand

logger = structlog.get_logger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
PLACEHOLDER = "@UPSTREAM_BRAND@"

LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS "_prisma_migrations" (
    "id"                  VARCHAR(36) PRIMARY KEY,
    "checksum"            VARCHAR(64)  NOT NULL,
    "finished_at"         TIMESTAMPTZ,
    "migration_name"      VARCHAR(255) NOT NULL,
    "logs"                TEXT,
    "rolled_back_at"      TIMESTAMPTZ,
    "started_at"          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    "applied_steps_count" INTEGER      NOT NULL DEFAULT 0
);
"""


@dataclass(frozen=True)
class Migration:
    name: str
    script: bytes
    checksum: str


def load_migrations(kind: str) -> list[Migration]:
    src = MIGRATIONS_DIR / kind
    migrations: list[Migration] = []
    brand = upstream_brand().encode()
    for sql_file in src.glob("*.sql"):
        patched = sql_file.read_bytes()
        script = patched.replace(PLACEHOLDER.encode(), brand)
        name = sql_file.stem.replace("BRANDPH", upstream_brand())
        migrations.append(
            Migration(
                name=name,
                script=script,
                checksum=hashlib.sha256(script).hexdigest(),
            )
        )
    migrations.sort(key=lambda m: m.name)
    return migrations


async def _ensure_ledger(conn: asyncpg.Connection) -> None:
    await conn.execute(LEDGER_DDL)


async def _applied(conn: asyncpg.Connection) -> dict[str, str]:
    rows = await conn.fetch(
        'SELECT "migration_name", "checksum", "finished_at" FROM "_prisma_migrations"'
    )
    failed = [r["migration_name"] for r in rows if r["finished_at"] is None]
    if failed:
        raise RuntimeError(
            f"Failed migrations present in ledger, resolve before deploying: {failed}"
        )
    return {r["migration_name"]: r["checksum"] for r in rows}


async def migrate(conn: asyncpg.Connection, kind: str) -> list[str]:
    """Apply pending migrations of one kind ('meta' | 'data'). Returns applied names."""
    await _ensure_ledger(conn)
    applied = await _applied(conn)
    newly_applied: list[str] = []
    for migration in load_migrations(kind):
        if migration.name in applied:
            if applied[migration.name] != migration.checksum:
                # upstream `prisma migrate deploy` keys the ledger by name and
                # skips rows already present, even across the meta/data split
                # where one migration name exists in both with different SQL
                logger.info(
                    "migration name already in ledger, skipping",
                    kind=kind,
                    name=migration.name,
                )
            continue
        row_id = str(uuid.uuid4())
        await conn.execute(
            'INSERT INTO "_prisma_migrations" ("id", "checksum", "migration_name")'
            " VALUES ($1, $2, $3)",
            row_id,
            migration.checksum,
            migration.name,
        )
        try:
            await conn.execute(migration.script.decode())
        except Exception as exc:
            await conn.execute(
                'UPDATE "_prisma_migrations" SET "logs" = $2 WHERE "id" = $1',
                row_id,
                str(exc),
            )
            raise
        await conn.execute(
            'UPDATE "_prisma_migrations" SET "finished_at" = now(),'
            ' "applied_steps_count" = 1 WHERE "id" = $1',
            row_id,
        )
        newly_applied.append(migration.name)
        logger.info("migration applied", kind=kind, name=migration.name)
    return newly_applied


async def migrate_all(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        for kind in ("meta", "data"):
            applied = await migrate(conn, kind)
            logger.info("migration batch done", kind=kind, applied=len(applied))
    finally:
        await conn.close()
