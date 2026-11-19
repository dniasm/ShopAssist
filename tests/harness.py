"""Test harness: ONE shared event loop for everything gRPC-aio, throwaway databases, service start/stop, app client.

Database tests skip with a clear message when Postgres is unreachable (see the `admin_url` fixture in conftest.py).
Throwaway databases are named test_<hex>_<svc>; they are recycled between tests and all dropped at the end of the session.
"""
import asyncio
import secrets
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo

def run(coro):
    """Run a coroutine to completion on THE shared services loop (see `get_loop_thread`)."""
    return get_loop_thread().call(coro, timeout=180)


def _write_dump(text: str) -> None:
    """Hang diagnostics go to a file: pytest captures stderr (even sys.__stderr__) while a test is running."""
    import tempfile
    with open(Path(tempfile.gettempdir()) / "shopassist_test_hang_dump.txt", "a", encoding="utf-8") as f:
        f.write(text + "\n")


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
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        try:
            return future.result(timeout)
        except TimeoutError:
            self.dump_tasks()  # a hung service call is a test-infrastructure bug: show who is stuck where
            raise

    def dump_tasks(self) -> None:
        import io

        async def snapshot():
            out = io.StringIO()
            for task in asyncio.all_tasks():
                out.write(f"-- {task.get_name()}: {task.get_coro()!r}\n")
                for frame in task.get_stack(limit=6):  # innermost last: where the task is waiting
                    out.write(f"     {frame.f_code.co_filename}:{frame.f_lineno} in {frame.f_code.co_name}\n")
            return out.getvalue()

        try:
            dump = asyncio.run_coroutine_threadsafe(snapshot(), self.loop).result(5)
            _write_dump("=== tasks on the services loop ===\n" + dump)
        except Exception as e:  # the loop itself may be blocked
            _write_dump(f"=== could not dump tasks: {e!r} ===")

    def close(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=5)


_DROPPER = ThreadPoolExecutor(max_workers=2, thread_name_prefix="db-dropper")  # drops run behind the tests, not in front of them
# A database cloned from a template is RECYCLED, not dropped: when its test ends it is emptied in the background
# (TRUNCATE ... RESTART IDENTITY, sequences restarted) and offered to the next test, whose service seeds it again
# (seeding runs whenever the tables are empty). Emptying a small database is several times cheaper than cloning one.
_FREE: dict[tuple[str, str], list[str]] = {}  # (admin url, template) -> emptied databases ready for reuse
_ORIGIN: dict[str, str] = {}  # database name -> the template it was cloned from


class TempDatabases:
    """Creates and drops throwaway databases through the admin URL."""

    def __init__(self, admin_url: str):
        self.admin_url = admin_url
        self._names: list[str] = []

    def create(self, service: str, template: str | None = None) -> str:
        """A new empty database, or a copy of `template` (already migrated and seeded, so there is nothing left to apply)."""
        if template is not None:
            free = _FREE.setdefault((self.admin_url, template), [])
            name = free.pop() if free else self._clone(service, template)
            _ORIGIN[name] = template
        else:
            name = self._clone(service, None)
        self._names.append(name)
        return make_conninfo(self.admin_url, dbname=name)

    def _clone(self, service: str, template: str | None) -> str:
        name = f"test_{secrets.token_hex(4)}_{service}"
        with psycopg.connect(self.admin_url, autocommit=True) as conn:
            conn.execute(f'CREATE DATABASE "{name}"' + (f' TEMPLATE "{template}"' if template else ""))
        return name

    def _recycle(self, name: str) -> None:
        """Empty a finished database and put it back in the free list; if that fails for any reason, drop it instead."""
        try:
            with psycopg.connect(self.admin_url, autocommit=True) as admin:  # nothing may still be connected (a stuck stop)
                admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()", (name,))
            with psycopg.connect(make_conninfo(self.admin_url, dbname=name), autocommit=True) as conn:
                tables = [r[0] for r in conn.execute(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename <> 'schema_migrations'")]
                if tables:
                    conn.execute("TRUNCATE " + ", ".join(f'"{t}"' for t in tables) + " RESTART IDENTITY CASCADE")
                for (sequence,) in conn.execute("SELECT sequencename FROM pg_sequences WHERE schemaname = 'public'").fetchall():
                    conn.execute(f'ALTER SEQUENCE "{sequence}" RESTART')
            _FREE.setdefault((self.admin_url, _ORIGIN[name]), []).append(name)
        except Exception:
            self._drop_now(name)

    def drop(self, name_or_url: str) -> None:
        """Drop in the background (the caller has already stopped whatever used it); pytest_sessionfinish waits for all."""
        name = conninfo_to_dict(name_or_url)["dbname"] if "=" in name_or_url or "://" in name_or_url else name_or_url
        if name in self._names:
            self._names.remove(name)
        _DROPPER.submit(self._recycle if name in _ORIGIN else self._drop_now, name)

    def _drop_now(self, name: str) -> None:
        with psycopg.connect(self.admin_url, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')

    def drop_all(self) -> None:
        for name in list(self._names):
            self.drop(name)

_LOOP_THREAD: list["LoopThread"] = []


def get_loop_thread() -> "LoopThread":
    """The ONE event loop every grpc.aio object in the test process lives on: the services, the test-side clients, and
    (through `AppClient`) the orchestrator app.

    Why one: grpc.aio is not safe across several event loops in one process. The suite used to give every `run()` and
    every TestClient its own loop; with them mixed, a gRPC completion occasionally woke the wrong loop and a call (or a
    server shutdown) waited forever, which showed up as an intermittent hang roughly one full run in three.
    """
    if not _LOOP_THREAD:
        _LOOP_THREAD.append(LoopThread())
    return _LOOP_THREAD[0]

@dataclass
class Running:
    """A started service: `addr` for clients, `url` for direct DB inspection by tests, `service` to stop it."""

    service: object
    addr: str
    url: str


# Migrating and seeding a database costs more than copying one, and almost every test needs several fresh ones. So the
# first test that wants a service database builds a migrated + seeded TEMPLATE (once per session, per service and seed
# data) and every later one is `CREATE DATABASE ... TEMPLATE`. Dropped in pytest_sessionfinish.
_TEMPLATES: dict[tuple, str] = {}
_TEMPLATE_ADMIN: list[str] = []


def _template_for(loop_thread: LoopThread, dbs: TempDatabases, name: str, create, opts: dict) -> str:
    key = (dbs.admin_url, name, create.__module__, str(opts.get("seed_dir")))
    if key not in _TEMPLATES:
        url = dbs.create(f"tpl_{name}")
        _TEMPLATE_ADMIN.append(dbs.admin_url)
        service = loop_thread.call(create(url, **opts))  # runs the migrations and the seed; never started
        loop_thread.call(service.stop(0.1))  # closes its pool: a template must have no open sessions to be copied
        _TEMPLATES[key] = conninfo_to_dict(url)["dbname"]
        dbs._names.remove(_TEMPLATES[key])  # owned by the session, not by the test that happened to build it
    return _TEMPLATES[key]

def start(loop_thread: LoopThread, dbs: TempDatabases, name: str, create, url: str | None = None, **opts) -> Running:
    url = url or dbs.create(name, template=_template_for(loop_thread, dbs, name, create, opts))
    service = loop_thread.call(create(url, **opts))
    loop_thread.call(service.start())
    return Running(service, f"127.0.0.1:{service.port}", url)


def stop(loop_thread: LoopThread, dbs: TempDatabases, running: Running) -> None:
    try:
        loop_thread.call(running.service.stop(0.2), timeout=25)
    finally:
        dbs.drop(running.url)  # always drop the database, even when stopping the service failed

# ---------------- the orchestrator app, on the shared loop ----------------

class _SharedPortal:
    """An anyio BlockingPortal living on the shared services loop, so a TestClient can run the app there."""

    def __init__(self, loop_thread: "LoopThread"):
        import anyio.from_thread
        self._ready = threading.Event()
        self.portal = None

        async def hold():
            async with anyio.from_thread.BlockingPortal() as portal:
                self.portal = portal
                self._ready.set()
                await portal.sleep_until_stopped()

        asyncio.run_coroutine_threadsafe(hold(), loop_thread.loop)
        assert self._ready.wait(10), "the shared portal did not start"


_PORTAL: list[_SharedPortal] = []


def AppClient(app):
    """A starlette TestClient whose app (lifespan, requests, WebSockets) runs on the shared services loop.

    Same behaviour as `TestClient(app)` used as a context manager, except for WHERE the app runs; see `get_loop_thread`.
    """
    import contextlib
    import math

    import anyio
    from fastapi.testclient import TestClient

    if not _PORTAL:
        _PORTAL.append(_SharedPortal(get_loop_thread()))

    class SharedLoopClient(TestClient):
        def __enter__(self):
            with contextlib.ExitStack() as stack:
                self.portal = portal = _PORTAL[0].portal
                stack.callback(lambda: setattr(self, "portal", None))
                send = anyio.create_memory_object_stream[object](math.inf)
                receive = anyio.create_memory_object_stream[object](math.inf)
                for channel in (*send, *receive):
                    stack.callback(channel.close)
                from starlette.testclient import StapledObjectStream
                self.stream_send = StapledObjectStream(*send)
                self.stream_receive = StapledObjectStream(*receive)
                self.task = portal.start_task_soon(self.lifespan)
                portal.call(self.wait_startup)
                stack.callback(lambda: portal.call(self.wait_shutdown))
                stack.callback(self._transport.close)
                self.exit_stack = stack.pop_all()
            return self

    return SharedLoopClient(app)


def release_databases() -> None:
    """End of session: finish the background emptying/dropping, then drop recycled databases and the templates."""
    _DROPPER.shutdown(wait=True)
    for (admin, _), names in _FREE.items():  # recycled databases nobody asked for again
        with psycopg.connect(admin, autocommit=True) as conn:
            for name in names:
                conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    if _TEMPLATES:
        with psycopg.connect(_TEMPLATE_ADMIN[0], autocommit=True) as conn:
            for template in _TEMPLATES.values():
                conn.execute(f'DROP DATABASE IF EXISTS "{template}" WITH (FORCE)')
