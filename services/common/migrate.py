"""Apply `migrations/NNN_name.sql` files in order and remember them in `schema_migrations`.

Each file runs in its own transaction under an advisory lock, so two processes starting at once
cannot apply the same migration twice, and a failed file leaves nothing half-applied.
"""
import logging
from pathlib import Path

from psycopg_pool import AsyncConnectionPool

log = logging.getLogger(__name__)

_LOCK_KEY = 7_265_001  # arbitrary app-wide key for the migration advisory lock
_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
)"""


def migration_files(directory: Path) -> list[Path]:
    """Files named NNN_*.sql, ordered by their number. Anything else in the folder is ignored."""
    return sorted(p for p in directory.glob("*.sql") if p.name[:3].isdigit() and p.name[3:4] == "_")


async def apply_migrations(pool: AsyncConnectionPool, directory: Path, max_version: int | None = None) -> list[str]:
    """Run every migration not yet recorded; returns the names applied by this call (empty on a rerun).

    `max_version` stops after migration NNN: tests build a database as it was before a migration, put data in it,
    then apply the migration to see what it does to rows that already exist.
    """
    applied_now: list[str] = []
    for path in migration_files(directory):
        if max_version is not None and int(path.name[:3]) > max_version:
            break
        async with pool.connection() as conn:
            await conn.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_KEY,))
            await conn.execute(_CREATE_TABLE)
            cur = await conn.execute("SELECT 1 FROM schema_migrations WHERE version = %s", (path.name,))
            if await cur.fetchone():
                continue
            await conn.execute(path.read_text(encoding="utf-8"))
            await conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (path.name,))
            applied_now.append(path.name)
            log.info("applied migration %s", path.name)
    return applied_now
