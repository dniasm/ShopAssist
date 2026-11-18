"""Database access shared by the services: one async pool per service, built from that service's own URL.

Each service reads only its own `<SVC>_DATABASE_URL`; the role in that URL can connect to nothing else,
which is what keeps the databases isolated (see tests/test_isolation.py).
"""
import os
from collections.abc import Mapping

from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool


def database_url(env_var: str, environ: Mapping[str, str] | None = None) -> str:
    """The URL named by `env_var`. The error names the variable, never its value (it holds a password)."""
    value = (environ if environ is not None else os.environ).get(env_var)
    if not value:
        raise RuntimeError(f"{env_var} is not set; run scripts/db_setup.py or set it in .env")
    return value


def safe_target(url: str) -> str:
    """`user@host:port/dbname` for logs; the password is dropped."""
    parts = conninfo_to_dict(url)
    return f"{parts.get('user', '?')}@{parts.get('host', 'localhost')}:{parts.get('port', 5432)}/{parts.get('dbname', '?')}"


async def create_pool(url: str, *, min_size: int = 1, max_size: int = 10) -> AsyncConnectionPool:
    """Open a pool whose connections return dict rows. Waits for the first connection so a bad URL fails at startup."""
    pool = AsyncConnectionPool(
        url, min_size=min_size, max_size=max_size, open=False, timeout=10,
        kwargs={"row_factory": dict_row},
    )
    await pool.open(wait=True, timeout=10)
    return pool
