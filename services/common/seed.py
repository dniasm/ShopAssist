"""Seed a service database from `data/seed/v2/*.json`, but only while its tables are still empty.

Why "only when empty": a restart must never overwrite data the running system has changed
(stock, carts); `scripts/db_setup.py --reset` is the way to start over.
"""
import json
import logging
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from psycopg import AsyncConnection, sql
from psycopg_pool import AsyncConnectionPool

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]


def seed_dir() -> Path:
    """`SEED_DIR` if set (relative paths resolve against the repo root), else data/seed/v2."""
    raw = os.environ.get("SEED_DIR")
    if not raw:
        return REPO_ROOT / "data" / "seed" / "v2"
    p = Path(raw)
    return p if p.is_absolute() else REPO_ROOT / p


def read_seed(directory: Path, filename: str) -> Any | None:
    """Parsed JSON, or None (with a warning) when the file is missing so the service can still start empty."""
    path = directory / filename
    if not path.exists():
        log.warning("seed file %s not found; leaving the tables empty", path)
        return None
    return json.loads(path.read_text(encoding="utf-8"))


async def seed_if_empty(
    pool: AsyncConnectionPool, table: str, loader: Callable[[AsyncConnection], Awaitable[int]]
) -> bool:
    """Run `loader(conn)` in one transaction if `table` has no rows. True when it ran.

    `table` is a constant chosen by the service, never user input. The advisory lock makes two
    services starting together seed once.
    """
    async with pool.connection() as conn:
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"seed:{table}",))
        cur = await conn.execute(sql.SQL("SELECT EXISTS (SELECT 1 FROM {})").format(sql.Identifier(table)))
        if (await cur.fetchone())["exists"]:
            return False
        count = await loader(conn)
        log.info("seeded %s with %d rows", table, count)
        return True
