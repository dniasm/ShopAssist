"""services/common: migrations, seeding, caller identity, health service, page tokens."""
import grpc
import pytest
from grpc_health.v1 import health_pb2, health_pb2_grpc

from tests import authchan
from services.catalog import db as catalog_db
from services.catalog.server import MIGRATIONS as CATALOG_MIGRATIONS, create_service as create_catalog
from services.common.auth import require_user
from services.common.db import create_pool, database_url, safe_target
from services.common.migrate import apply_migrations, migration_files
from services.common.seed import seed_if_empty
from services.common.util import decode_offset, encode_offset
from tests.conftest import run
from tests.fixtures import PRODUCTS


def test_migrations_are_idempotent_and_recorded_once(temp_dbs):
    async def scenario(url):
        pool = await create_pool(url)
        try:
            first = await apply_migrations(pool, CATALOG_MIGRATIONS)
            second = await apply_migrations(pool, CATALOG_MIGRATIONS)
            async with pool.connection() as conn:
                rows = await (await conn.execute("SELECT version FROM schema_migrations ORDER BY version")).fetchall()
            return first, second, [r["version"] for r in rows]
        finally:
            await pool.close()

    first, second, recorded = run(scenario(temp_dbs.create("catalog")))
    assert first == recorded == [p.name for p in migration_files(CATALOG_MIGRATIONS)]
    assert second == []


def test_concurrent_startups_apply_each_migration_once(temp_dbs):
    async def scenario(url):
        pools = [await create_pool(url, max_size=2) for _ in range(4)]
        try:
            import asyncio
            results = await asyncio.gather(*(apply_migrations(p, CATALOG_MIGRATIONS) for p in pools))
            return sorted(len(r) for r in results)
        finally:
            for p in pools:
                await p.close()

    assert run(scenario(temp_dbs.create("catalog"))) == [0, 0, 0, len(migration_files(CATALOG_MIGRATIONS))]


def test_failed_migration_is_not_recorded(temp_dbs, tmp_path):
    (tmp_path / "001_ok.sql").write_text("CREATE TABLE a (id int);")
    (tmp_path / "002_bad.sql").write_text("CREATE TABLE b (id int); SELECT * FROM nonexistent;")

    async def scenario(url):
        pool = await create_pool(url)
        try:
            with pytest.raises(Exception):
                await apply_migrations(pool, tmp_path)
            async with pool.connection() as conn:
                versions = [r["version"] for r in await (await conn.execute("SELECT version FROM schema_migrations")).fetchall()]
                b = (await (await conn.execute("SELECT to_regclass('b') AS t")).fetchone())["t"]
            return versions, b
        finally:
            await pool.close()

    versions, b_table = run(scenario(temp_dbs.create("catalog")))
    assert versions == ["001_ok.sql"] and b_table is None  # 002 rolled back entirely


def test_seed_runs_only_while_the_table_is_empty(temp_dbs, seed_dir):
    async def scenario(url):
        pool = await create_pool(url)
        try:
            await apply_migrations(pool, CATALOG_MIGRATIONS)
            from services.common.seed import read_seed
            data = read_seed(seed_dir, "catalog.json")
            first = await seed_if_empty(pool, "products", lambda c: catalog_db.load_seed(c, data))
            second = await seed_if_empty(pool, "products", lambda c: catalog_db.load_seed(c, data))
            async with pool.connection() as conn:
                n = (await (await conn.execute("SELECT count(*) AS n FROM products")).fetchone())["n"]
            return first, second, n
        finally:
            await pool.close()

    assert run(scenario(temp_dbs.create("catalog"))) == (True, False, len(PRODUCTS))


def test_service_restart_does_not_reseed_or_duplicate(loop_thread, temp_dbs, seed_dir):
    url = temp_dbs.create("catalog")
    for _ in range(2):
        service = loop_thread.call(create_catalog(url, seed_dir=seed_dir))
        loop_thread.call(service.stop(0.1))
    import psycopg
    with psycopg.connect(url) as conn:
        assert conn.execute("SELECT count(*) FROM products").fetchone()[0] == len(PRODUCTS)


def test_missing_seed_file_leaves_tables_empty_but_service_starts(loop_thread, temp_dbs, tmp_path):
    url = temp_dbs.create("catalog")
    service = loop_thread.call(create_catalog(url, seed_dir=tmp_path))  # empty directory
    loop_thread.call(service.stop(0.1))
    import psycopg
    with psycopg.connect(url) as conn:
        assert conn.execute("SELECT count(*) FROM products").fetchone()[0] == 0


class FakeContext:
    """The two ServicerContext members `require_user` touches."""

    def __init__(self, metadata):
        self._md = metadata

    def invocation_metadata(self):
        return self._md

    async def abort(self, code, details):
        raise grpc.RpcError(code, details)


def test_require_user_reads_x_user_id():
    assert run(require_user(FakeContext((("x-other", "1"), ("x-user-id", "U-07"))))) == "U-07"


@pytest.mark.parametrize("md", [(), (("x-user-id", "  "),), (("x-other", "U-1"),)])
def test_require_user_aborts_unauthenticated_when_missing(md):
    with pytest.raises(grpc.RpcError) as e:
        run(require_user(FakeContext(md)))
    assert e.value.args[0] == grpc.StatusCode.UNAUTHENTICATED


def test_every_service_serves_grpc_health(catalog_running):
    with authchan.channel(catalog_running.addr) as ch:
        stub = health_pb2_grpc.HealthStub(ch)
        for name in ("", "shopassist.v2.CatalogService"):
            assert stub.Check(health_pb2.HealthCheckRequest(service=name)).status == health_pb2.HealthCheckResponse.SERVING
        with pytest.raises(grpc.RpcError) as e:
            stub.Check(health_pb2.HealthCheckRequest(service="shopassist.v2.Nope"))
        assert e.value.code() == grpc.StatusCode.NOT_FOUND


def test_page_tokens_round_trip_and_reject_foreign_tokens():
    assert decode_offset(encode_offset(48)) == 48 and decode_offset("") == 0
    for bad in ("!!", "bm9wZQ==", encode_offset(1)[:-3]):
        with pytest.raises(ValueError):
            decode_offset(bad)


def test_database_url_error_never_contains_a_value():
    with pytest.raises(RuntimeError, match="CATALOG_DATABASE_URL is not set"):
        database_url("CATALOG_DATABASE_URL", {})
    assert safe_target("postgresql://svc:s3cret@localhost:5432/catalog") == "svc@localhost:5432/catalog"


def test_cancelling_serve_forever_shuts_the_service_down_gracefully(loop_thread, temp_dbs, seed_dir):
    """Ctrl+C reaches the service as task cancellation on Windows; it must still stop the server and close the pool."""
    import asyncio

    async def scenario():
        service = await create_catalog(temp_dbs.create("catalog"), seed_dir=seed_dir)
        task = asyncio.create_task(service.serve_forever())
        await asyncio.sleep(0.3)  # serving
        async with authchan.aio_channel(f"127.0.0.1:{service.port}") as ch:
            stub = health_pb2_grpc.HealthStub(ch)
            before = (await stub.Check(health_pb2.HealthCheckRequest(service=""))).status
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return before, service.pool.closed

    before, pool_closed = loop_thread.call(scenario())
    assert before == health_pb2.HealthCheckResponse.SERVING and pool_closed


# ---------------- idempotency fingerprints ----------------

def test_fingerprint_ignores_key_order_but_not_content():
    from services.common.idempotency import fingerprint
    assert fingerprint({"a": 1, "b": [1, 2]}) == fingerprint({"b": [1, 2], "a": 1})
    assert fingerprint({"a": 1}) != fingerprint({"a": 2})
    assert fingerprint([["SKU-1", 2]]) != fingerprint([["SKU-1", 3]])
    assert len(fingerprint({})) == 64  # sha256 hex


def test_check_replay_accepts_same_and_missing_fingerprints_and_rejects_different():
    from services.common.idempotency import KeyReused, check_replay, fingerprint
    f = fingerprint({"x": 1})
    check_replay("k", f, f)
    check_replay("k", None, f)  # rows written before fingerprints existed cannot be compared
    with pytest.raises(KeyReused, match="idempotency key reused with a different request"):
        check_replay("k", f, fingerprint({"x": 2}))


# ---------------- a stuck gRPC shutdown must not wedge the process ----------------

def test_stop_gives_up_on_a_gRPC_server_that_will_not_stop_and_still_closes_everything(monkeypatch):
    """Found by the suite hang: `grpc.aio.Server.stop()` can wait forever on an open streaming call. Stop is bounded."""
    import asyncio
    from services.common import server as server_module

    class StuckServer:
        async def stop(self, grace):
            await asyncio.Event().wait()  # never returns

    class FakePool:
        closed = False

        async def close(self):
            self.closed = True

    async def scenario():
        service = server_module.Service("stuck", FakePool())
        service.server = StuckServer()
        order = []
        service.add_pre_stop(lambda: _record(order, "pre"))
        service.add_closer(lambda: _record(order, "closer"))
        await asyncio.wait_for(service.stop(grace=0.1), timeout=5)  # the test itself must not hang if the fix is missing
        return order, service.pool.closed

    async def _record(order, name):
        order.append(name)

    monkeypatch.setattr(server_module, "STOP_PATIENCE_SECONDS", 0.3)
    order, closed = run(scenario())
    assert order == ["pre", "closer"] and closed  # pre-stop hooks first, then (after giving up waiting) closers and the pool
