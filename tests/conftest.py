"""Shared fixtures. The machinery behind them (the one shared event loop, throwaway/recycled databases, starting and
stopping services, the app client) is in tests/harness.py; this file only turns it into pytest fixtures.

No pytest-asyncio: tests are plain functions that call real services over real sockets.
"""
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pytest
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

load_dotenv(REPO_ROOT / ".env")
# Logins: seeded users get this password, and hashing uses few rounds (600,000 per check would slow every sign-in).
os.environ["DEMO_PASSWORD"] = "demo-password-for-tests"
os.environ["PASSWORD_ROUNDS"] = "1000"

import grpc  # noqa: E402
import psycopg  # noqa: E402

from shopassist_proto import shopassist_pb2_grpc as pb_grpc  # noqa: E402
from tests import harness  # noqa: E402
from tests.fixtures import write_seed_dir  # noqa: E402
# re-exported: many tests import these from tests.conftest
from tests.harness import (AppClient, LoopThread, Running, TempDatabases, _template_for, get_loop_thread, run,  # noqa: E402,F401
                           start, stop)


def pytest_sessionfinish(session, exitstatus):
    harness.release_databases()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Print a failure the moment it happens when TEST_LIVE_FAILURES is set (pytest's own report comes only at the
    end, and a hung teardown can stop it from ever coming): this is how the intermittent suite hang was traced."""
    outcome = yield
    report = outcome.get_result()
    if report.failed and os.environ.get("TEST_LIVE_FAILURES"):
        sys.__stderr__.write(f"\n===== LIVE FAILURE {report.nodeid} ({report.when}) =====\n{report.longreprtext}\n")
        sys.__stderr__.flush()


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
    lt = get_loop_thread()
    yield lt
    lt.close()
    harness._LOOP_THREAD.clear()


@pytest.fixture(scope="session")
def seed_dir(tmp_path_factory):
    return write_seed_dir(tmp_path_factory.mktemp("seed"))

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


@pytest.fixture
def fresh_customer_running(loop_thread, temp_dbs, seed_dir):
    """A customer service of its own, for tests that create accounts."""
    from services.customer.server import create_service
    r = start(loop_thread, temp_dbs, "customer", create_service, seed_dir=seed_dir)
    yield r
    stop(loop_thread, temp_dbs, r)


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


@pytest.fixture
def payment_running(loop_thread, temp_dbs, seed_dir):
    """A fresh payment service per test, with no artificial latency."""
    from services.payment.server import create_service
    r = start(loop_thread, temp_dbs, "payment", create_service, seed_dir=seed_dir, latency_ms=0)
    yield r
    stop(loop_thread, temp_dbs, r)


@pytest.fixture
def payment(payment_running):
    with grpc.insecure_channel(payment_running.addr) as ch:
        yield pb_grpc.PaymentServiceStub(ch)


@dataclass
class Stack:
    """Inventory + payment + order services wired together (the catalog is shared), each on its own database."""

    inventory: Running
    payment: Running
    order: Running
    loop_thread: LoopThread
    dbs: TempDatabases
    create_order: object
    order_opts: dict

    def restart_order(self, **overrides) -> Running:
        """Stop the order service (as a crash would, in-flight sagas die with it) and start a new one on the same database."""
        self.loop_thread.call(self.order.service.stop(0.1))
        opts = {**self.order_opts, **overrides}
        service = self.loop_thread.call(self.create_order(self.order.url, **opts))
        self.loop_thread.call(service.start())
        self.order = Running(service, f"127.0.0.1:{service.port}", self.order.url)
        return self.order


@pytest.fixture
def make_stack(loop_thread, temp_dbs, seed_dir, catalog_running):
    from services.inventory.server import create_service as create_inventory
    from services.order.server import create_service as create_order
    from services.payment.server import create_service as create_payment

    stacks: list[Stack] = []

    def make(*, payment_latency_ms=0, fault=None, reservation_ttl_seconds=0, recovery_min_age_seconds=60, **order_extra) -> Stack:
        inv_opts = dict(seed_dir=seed_dir, sweep_seconds=3600)
        pay_opts = dict(seed_dir=seed_dir, latency_ms=payment_latency_ms)
        # the three databases are cloned side by side (each clone is mostly waiting on the disk)
        templates = {n: _template_for(loop_thread, temp_dbs, n, c, o) for n, c, o in (
            ("inventory", create_inventory, inv_opts), ("payment", create_payment, pay_opts),
            ("orders", create_order, dict(seed_dir=seed_dir, catalog_addr=catalog_running.addr, inventory_addr="x", payment_addr="y", **order_extra)))}
        with ThreadPoolExecutor(3) as pool:
            urls = dict(zip(templates, pool.map(lambda n: temp_dbs.create(n, template=templates[n]), templates)))
        inventory = start(loop_thread, temp_dbs, "inventory", create_inventory, url=urls["inventory"], **inv_opts)
        payment = start(loop_thread, temp_dbs, "payment", create_payment, url=urls["payment"], **pay_opts)
        opts = dict(
            seed_dir=seed_dir, catalog_addr=catalog_running.addr, inventory_addr=inventory.addr, payment_addr=payment.addr,
            fault=fault, reservation_ttl_seconds=reservation_ttl_seconds, recovery_min_age_seconds=recovery_min_age_seconds,
            fulfilment=False,  # tests advance orders explicitly (fulfilment.advance_due) unless they opt in
        )
        opts.update(order_extra)
        order = start(loop_thread, temp_dbs, "orders", create_order, url=urls["orders"], **opts)
        stack = Stack(inventory, payment, order, loop_thread, temp_dbs, create_order, opts)
        stacks.append(stack)
        return stack

    yield make
    for s in stacks:
        for running in (s.order, s.payment, s.inventory):
            stop(loop_thread, temp_dbs, running)


@pytest.fixture(scope="session")
def payment_ro_running(loop_thread, session_dbs, seed_dir):
    """Session-wide payment + order services for read-only API tests (health, wiring); mutation tests use `make_stack`."""
    from services.payment.server import create_service
    r = start(loop_thread, session_dbs, "payment", create_service, seed_dir=seed_dir, latency_ms=0)
    yield r
    stop(loop_thread, session_dbs, r)


@pytest.fixture(scope="session")
def order_ro_running(loop_thread, session_dbs, seed_dir, catalog_running, inventory_ro_running, payment_ro_running):
    from services.order.server import create_service
    r = start(
        loop_thread, session_dbs, "orders", create_service, seed_dir=seed_dir, catalog_addr=catalog_running.addr,
        inventory_addr=inventory_ro_running.addr, payment_addr=payment_ro_running.addr,
    )
    yield r
    stop(loop_thread, session_dbs, r)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Print a failure the moment it happens when TEST_LIVE_FAILURES is set (pytest's own report comes only at the
    end, and a hung teardown can stop it from ever coming): this is how the intermittent suite hang was traced."""
    outcome = yield
    report = outcome.get_result()
    if report.failed and os.environ.get("TEST_LIVE_FAILURES"):
        sys.__stderr__.write(f"\n===== LIVE FAILURE {report.nodeid} ({report.when}) =====\n{report.longreprtext}\n")
        sys.__stderr__.flush()
