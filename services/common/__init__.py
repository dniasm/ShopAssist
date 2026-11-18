"""Small helpers shared by the three gRPC services (DB access, seeding, serving)."""
import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SEED_DIR = REPO_ROOT / "data" / "seed"


def env_path(name: str, default: str) -> Path:
    """A path from the environment; relative paths are resolved against the repo root, not the cwd."""
    p = Path(os.environ.get(name) or default)
    return p if p.is_absolute() else REPO_ROOT / p


def bind_address(env_name: str, default: str) -> str:
    """The service listens on the same address variable the orchestrator dials (e.g. localhost:50051)."""
    return os.environ.get(env_name) or default


@contextmanager
def connection(db_path):
    """Short-lived SQLite connection (one per call, so gRPC worker threads never share one)."""
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def prepare_db(db_path, schema: str, count_sql: str, seed_path, load_seed) -> None:
    """Create tables; if the DB has no rows yet, fill it from the seed JSON via load_seed(conn, records)."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connection(db_path) as conn:
        conn.executescript(schema)
        if conn.execute(count_sql).fetchone()[0] == 0:
            records = json.loads(Path(seed_path).read_text(encoding="utf-8"))
            load_seed(conn, records)
            logging.getLogger(__name__).info("seeded %s from %s (%d records)", db_path, seed_path, len(records))


def serve(server, name: str, address: str) -> None:
    """Start a grpc server and block until Ctrl+C."""
    server.start()
    logging.getLogger(name).info("%s listening on %s", name, address)
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        server.stop(grace=1).wait()
