"""Shared fixtures: throwaway Postgres databases and real grpc.aio services running on a background loop.

Why a background loop thread: grpc.aio servers and psycopg pools must live on one (selector) event loop,
while tests are plain sync functions that call them over real sockets. Concurrency tests open their own
grpc.aio channels inside `asyncio.run`. No pytest-asyncio is needed.

Database tests skip with a clear message when Postgres is unreachable. Throwaway databases are named
test_<hex>_<svc> and dropped at teardown even when a test fails.
"""
import asyncio
import os
import secrets
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

import pytest
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

load_dotenv(REPO_ROOT / ".env")

import grpc  # noqa: E402
import psycopg  # noqa: E402
from psycopg.conninfo import conninfo_to_dict, make_conninfo  # noqa: E402

from shopassist_proto import shopassist_pb2_grpc as pb_grpc  # noqa: E402
from tests.fixtures import write_seed_dir  # noqa: E402


def run(coro):
    """Run a coroutine to completion on a selector loop (psycopg async needs one on Windows)."""
    return asyncio.run(coro, loop_factory=asyncio.SelectorEventLoop)


class LoopThread:
    """An event loop in a daemon thread; `call` runs a coroutine on it and returns the result."""

    def __init__(self):
        self.loop = asyncio.SelectorEventLoop()
        self._thread = threading.Thread(target=self._run, daemon=True, name="test-services-loop")
        self._thread.start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def call(self, coro, timeout: float = 60):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def close(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=5)


class TempDatabases:
    """Creates and drops throwaway databases through the admin URL."""

    def __init__(self, admin_url: str):
        self.admin_url = admin_url
        self._names: list[str] = []

    def create(self, service: str) -> str:
        name = f"test_{secrets.token_hex(4)}_{service}"
        with psycopg.connect(self.admin_url, autocommit=True) as conn:
            conn.execute(f'CREATE DATABASE "{name}"')
        self._names.append(name)
        return make_conninfo(self.admin_url, dbname=name)

    def drop(self, name_or_url: str) -> None:
        name = conninfo_to_dict(name_or_url)["dbname"] if "=" in name_or_url or "://" in name_or_url else name_or_url
        with psycopg.connect(self.admin_url, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        if name in self._names:
            self._names.remove(name)

    def drop_all(self) -> None:
        for name in list(self._names):
            self.drop(name)


@pytest.fixture(scope="session")
def admin_url():
    url = os.environ.get("PG_ADMIN_URL")
    if not url:
        pytest.skip("PG_ADMIN_URL is not set: database tests need the local Postgres (see scripts/pg.py)")
    try:
        psycopg.connect(url, connect_timeout=3).close()
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL is not reachable on the PG_ADMIN_URL host: start it with `python scripts/pg.py start`")
    return url


@pytest.fixture(scope="session")
def session_dbs(admin_url):
    dbs = TempDatabases(admin_url)
    yield dbs
    dbs.drop_all()


@pytest.fixture
def temp_dbs(admin_url):
    dbs = TempDatabases(admin_url)
    yield dbs
    dbs.drop_all()


@pytest.fixture(scope="session")
def loop_thread():
    lt = LoopThread()
    yield lt
    lt.close()


@pytest.fixture(scope="session")
def seed_dir(tmp_path_factory):
    return write_seed_dir(tmp_path_factory.mktemp("seed"))


@dataclass
class Running:
    """A started service: `addr` for clients, `url` for direct DB inspection by tests, `service` to stop it."""

    service: object
    addr: str
    url: str


def start(loop_thread: LoopThread, dbs: TempDatabases, name: str, create, **opts) -> Running:
    url = dbs.create(name)
    service = loop_thread.call(create(url, **opts))
    loop_thread.call(service.start())
    return Running(service, f"127.0.0.1:{service.port}", url)


def stop(loop_thread: LoopThread, dbs: TempDatabases, running: Running) -> None:
    loop_thread.call(running.service.stop(0.2))
    dbs.drop(running.url)


@pytest.fixture(scope="session")
def catalog_running(loop_thread, session_dbs, seed_dir):
    from services.catalog.server import create_service
    r = start(loop_thread, session_dbs, "catalog", create_service, seed_dir=seed_dir)
    yield r
    stop(loop_thread, session_dbs, r)


@pytest.fixture(scope="session")
def customer_running(loop_thread, session_dbs, seed_dir):
    from services.customer.server import create_service
    r = start(loop_thread, session_dbs, "customer", create_service, seed_dir=seed_dir)
    yield r
    stop(loop_thread, session_dbs, r)


@pytest.fixture(scope="session")
def inventory_ro_running(loop_thread, session_dbs, seed_dir):
    """Seeded inventory that read-only tests (orchestrator endpoints) share; mutation tests use `inventory_running`."""
    from services.inventory.server import create_service
    r = start(loop_thread, session_dbs, "inventory", create_service, seed_dir=seed_dir, sweep_seconds=3600)
    yield r
    stop(loop_thread, session_dbs, r)


@pytest.fixture
def inventory_running(loop_thread, temp_dbs, seed_dir):
    """A fresh seeded inventory per test; the sweeper is effectively off so tests call it explicitly."""
    from services.inventory.server import create_service
    r = start(loop_thread, temp_dbs, "inventory", create_service, seed_dir=seed_dir, sweep_seconds=3600)
    yield r
    stop(loop_thread, temp_dbs, r)


@pytest.fixture(scope="session")
def catalog(catalog_running):
    with grpc.insecure_channel(catalog_running.addr) as ch:
        yield pb_grpc.CatalogServiceStub(ch)


@pytest.fixture
def inventory(inventory_running):
    with grpc.insecure_channel(inventory_running.addr) as ch:
        yield pb_grpc.InventoryServiceStub(ch)


@pytest.fixture(scope="session")
def customer(customer_running):
    with grpc.insecure_channel(customer_running.addr) as ch:
        yield pb_grpc.CustomerServiceStub(ch)
