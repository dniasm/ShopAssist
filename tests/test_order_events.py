"""The outbox, NOTIFY and WatchOrders: every status change is announced, and watchers see each event exactly once."""
import asyncio
import re
import threading
import time
from pathlib import Path

import grpc
import psycopg
import pytest

from shopassist_proto import shopassist_pb2 as pb
from shopassist_proto import shopassist_pb2_grpc as pb_grpc
from tests.conftest import run
from tests.helpers import (FULL_JOURNEY, advance, as_user, checkout, code_of, collect_events, get_order, order_statuses, rows,
                           sku_with, subscribed)

ORDER_SRC = Path(__file__).resolve().parents[1] / "services" / "order"


# ---------------- one door for status changes ----------------

def test_only_change_status_updates_an_orders_status_and_only_event_writes_the_outbox():
    """Every status change must write its outbox row and NOTIFY in the same transaction, so there is exactly one
    function that does it, and nothing else in the service may touch `orders.status`."""
    update_status = re.compile(r"UPDATE\s+orders\s+SET[^\"]*?\bstatus\s*=", re.I | re.S)
    outbox_insert = re.compile(r"INSERT\s+INTO\s+order_events", re.I)
    updates, inserts = [], []
    for path in ORDER_SRC.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for m in update_status.finditer(text):
            updates.append((path.name, text.rfind("def ", 0, m.start())))
        for m in outbox_insert.finditer(text):
            inserts.append((path.name, text[text.rfind("def ", 0, m.start()):].split("(")[0]))
    func_names = {(f, re.match(r"def (\w+)", (ORDER_SRC / f).read_text(encoding="utf-8")[pos:]).group(1)) for f, pos in updates}
    assert func_names == {("db.py", "change_status")}, func_names
    assert {i[1] for i in inserts} <= {"def _event", "async def _event", "def load_seed", "async def load_seed"}, inserts


def test_every_outbox_row_is_followed_by_a_notify_with_its_event_id(make_stack):
    stack = make_stack()
    with psycopg.connect(stack.order.url, autocommit=True) as listener:
        listener.execute("LISTEN order_events")
        order = run(checkout(stack.order.addr, "U-01", "k-n", [(sku_with(4), 1)]))[-1].order
        notified = [int(n.payload) for n in listener.notifies(timeout=5, stop_after=2)]
    ids = [r[0] for r in rows(stack.order.url, "SELECT event_id FROM order_events WHERE order_id = %s ORDER BY event_id", (order.order_id,))]
    assert notified == ids and len(ids) == 2  # PENDING and CONFIRMED, each announced once


def test_a_rolled_back_status_change_announces_nothing(make_stack):
    """NOTIFY is transactional: if the change is rolled back there is no event row and no notification."""
    stack = make_stack()
    from services.order import db
    order = run(checkout(stack.order.addr, "U-01", "k-rb", [(sku_with(4), 1)]))[-1].order
    before = rows(stack.order.url, "SELECT count(*) FROM order_events")

    async def attempt():
        async with stack.order.service.pool.connection() as conn:
            assert await db.change_status(conn, order.order_id, ("CONFIRMED",), "PACKED", "x")
            await conn.rollback()

    with psycopg.connect(stack.order.url, autocommit=True) as listener:
        listener.execute("LISTEN order_events")
        stack.loop_thread.call(attempt())
        assert list(listener.notifies(timeout=0.5)) == []
    assert rows(stack.order.url, "SELECT count(*) FROM order_events") == before
    assert get_order(stack.order.addr, "U-01", order.order_id).status == pb.CONFIRMED


# ---------------- WatchOrders ----------------

def test_watch_replays_after_an_id_then_streams_live_events_in_order(make_stack):
    stack = make_stack()
    run(checkout(stack.order.addr, "U-01", "k-w1", [(sku_with(4), 1)]))
    first = rows(stack.order.url, "SELECT max(event_id) FROM order_events")[0][0]
    order2 = run(checkout(stack.order.addr, "U-01", "k-w2", [(sku_with(4), 1)]))[-1].order  # happens after `first`

    async def scenario():
        seen = asyncio.create_task(collect_events(
            stack.order.addr, "U-01", after=first, stop=lambda ev: sum(1 for e in ev if e.order_id == order2.order_id) >= 4))
        await subscribed(stack, 1)  # registered: the replay (PENDING, CONFIRMED of order 2) is on its way, the rest is live
        await asyncio.get_running_loop().run_in_executor(None, advance, stack)
        await asyncio.get_running_loop().run_in_executor(None, advance, stack)
        return await seen

    events = run(scenario())
    assert [pb.OrderStatus.Name(e.status) for e in events if e.order_id == order2.order_id] == ["PENDING", "CONFIRMED", "PACKED", "SHIPPED"]
    assert [e.event_id for e in events] == sorted(e.event_id for e in events)


def test_after_minus_one_means_only_what_happens_from_now(make_stack):
    stack = make_stack()
    run(checkout(stack.order.addr, "U-01", "k-m1", [(sku_with(4), 1)]))

    async def scenario():
        seen = asyncio.create_task(collect_events(stack.order.addr, "U-01", after=-1, stop=lambda ev: len(ev) >= 1, timeout=10))
        await subscribed(stack, 1)
        await asyncio.get_running_loop().run_in_executor(None, advance, stack)
        return await seen

    events = run(scenario())
    assert [pb.OrderStatus.Name(e.status) for e in events] == ["PACKED"]  # no replay of the earlier PENDING/CONFIRMED


# Invariant: a watcher that connects while another writer is changing statuses sees every event exactly once:
# no gap between the replay and the live stream, no duplicate from the overlap.
def test_replay_then_live_has_no_gaps_or_duplicates_under_a_concurrent_writer(make_stack):
    stack = make_stack()
    for i in range(6):
        run(checkout(stack.order.addr, "U-01", f"k-c{i}", [(sku_with(4, skip=i), 1)]))
    finished = threading.Event()

    def writer():  # keeps advancing every order while the watcher connects, in small interleaved passes
        while not finished.is_set():
            if advance(stack) == 0:
                break
            time.sleep(0.01)

    thread = threading.Thread(target=writer)
    thread.start()
    time.sleep(0.03)  # connect in the middle of the writer's run
    events = run(collect_events(stack.order.addr, "U-01", after=0, timeout=3))  # replay + live while the writer runs
    finished.set()
    thread.join()
    expected = [r[0] for r in rows(stack.order.url, "SELECT event_id FROM order_events WHERE user_id = 'U-01' ORDER BY event_id")]
    got_ids = [e.event_id for e in events]
    assert len(got_ids) == len(set(got_ids)), "a watcher saw an event twice"
    assert sorted(got_ids) == expected, "a watcher missed an event"
    for (order_id,) in rows(stack.order.url, "SELECT order_id FROM orders WHERE order_id > 'O-2'"):
        assert order_statuses(stack.order.url, order_id) == FULL_JOURNEY  # and each order really made the whole journey
        assert [pb.OrderStatus.Name(e.status) for e in events if e.order_id == order_id] == FULL_JOURNEY  # in order


def test_two_watchers_of_one_order_each_see_every_step_once(make_stack):
    stack = make_stack()
    order = run(checkout(stack.order.addr, "U-01", "k-two", [(sku_with(4), 1)]))[-1].order

    async def scenario():
        stop = lambda ev: any(e.status == pb.DELIVERED for e in ev)  # noqa: E731
        a = asyncio.create_task(collect_events(stack.order.addr, "U-01", after=0, order_id=order.order_id, stop=stop))
        b = asyncio.create_task(collect_events(stack.order.addr, "U-01", after=-1, order_id=order.order_id, stop=stop))
        await subscribed(stack, 2)
        loop = asyncio.get_running_loop()
        for _ in range(4):
            await loop.run_in_executor(None, advance, stack)
            await asyncio.sleep(0.05)
        return await a, await b

    a, b = run(scenario())
    assert [pb.OrderStatus.Name(e.status) for e in a] == FULL_JOURNEY[:1] + FULL_JOURNEY[1:]  # replayed PENDING, CONFIRMED + live
    assert [pb.OrderStatus.Name(e.status) for e in b] == FULL_JOURNEY[2:]  # from now: PACKED .. DELIVERED
    assert [e.event_id for e in a][2:] == [e.event_id for e in b]


def test_watching_is_scoped_to_the_caller_and_needs_an_identity(make_stack):
    stack = make_stack()
    order = run(checkout(stack.order.addr, "U-01", "k-own", [(sku_with(4), 1)]))[-1].order
    with grpc.insecure_channel(stack.order.addr) as ch:
        stub = pb_grpc.OrderServiceStub(ch)
        assert code_of(lambda: next(stub.WatchOrders(pb.WatchOrdersRequest(order_id=order.order_id), metadata=as_user("U-02")))) == grpc.StatusCode.NOT_FOUND
        assert code_of(lambda: next(stub.WatchOrders(pb.WatchOrdersRequest()))) == grpc.StatusCode.UNAUTHENTICATED
    stranger = run(collect_events(stack.order.addr, "U-02", after=0, timeout=3.0))
    assert order.order_id not in {e.order_id for e in stranger}  # U-02 sees only its own (seeded) history
    assert {e.order_id for e in stranger} == {"O-10002"}
    owner = run(collect_events(stack.order.addr, "U-01", after=0, timeout=3.0))
    assert order.order_id in {e.order_id for e in owner}


def test_the_listener_reconnects_after_postgres_drops_it_and_misses_nothing(make_stack):
    stack = make_stack()
    order = run(checkout(stack.order.addr, "U-01", "k-drop", [(sku_with(4), 1)]))[-1].order
    dbname = rows(stack.order.url, "SELECT current_database()")[0][0]

    def kill_listener():
        pids = rows(stack.order.url, "SELECT pid FROM pg_stat_activity WHERE datname = %s AND query ILIKE 'LISTEN%%'", (dbname,))
        assert pids, "the order service holds no LISTEN connection"
        with psycopg.connect(stack.order.url, autocommit=True) as c:
            for (pid,) in pids:
                c.execute("SELECT pg_terminate_backend(%s)", (pid,))

    async def scenario():
        stop = lambda ev: any(e.status == pb.DELIVERED for e in ev)  # noqa: E731
        seen = asyncio.create_task(collect_events(stack.order.addr, "U-01", after=-1, order_id=order.order_id, stop=stop, timeout=20))
        loop = asyncio.get_running_loop()
        await subscribed(stack, 1)
        await loop.run_in_executor(None, advance, stack)       # PACKED reaches the watcher live
        await loop.run_in_executor(None, kill_listener)        # Postgres drops the listening connection
        await loop.run_in_executor(None, advance, stack)       # SHIPPED happens during the outage
        await asyncio.sleep(2.5)                               # the listener reconnects and catches up
        await loop.run_in_executor(None, advance, stack)
        await loop.run_in_executor(None, advance, stack)
        return await seen

    events = run(scenario())
    assert [pb.OrderStatus.Name(e.status) for e in events] == ["PACKED", "SHIPPED", "OUT_FOR_DELIVERY", "DELIVERED"]


# ---------------- reading never writes ----------------

def test_reading_orders_never_writes(make_stack):
    stack = make_stack()
    order = run(checkout(stack.order.addr, "U-01", "k-read", [(sku_with(4), 1)]))[-1].order
    snapshot = lambda: (rows(stack.order.url, "SELECT count(*), max(event_id) FROM order_events"),  # noqa: E731
                        rows(stack.order.url, "SELECT order_id, status, saga_step, updated_at FROM orders ORDER BY order_id"))
    before = snapshot()
    with grpc.insecure_channel(stack.order.addr) as ch:
        stub = pb_grpc.OrderServiceStub(ch)
        for _ in range(25):
            stub.GetOrder(pb.GetOrderRequest(order_id=order.order_id), metadata=as_user("U-01"))
            stub.ListOrders(pb.ListOrdersRequest(), metadata=as_user("U-01"))
    run(collect_events(stack.order.addr, "U-01", after=0, timeout=0.5))
    assert snapshot() == before  # no new events, no touched rows, not even updated_at


def test_a_from_now_stream_opens_with_a_cursor_proving_it_is_registered(make_stack):
    stack = make_stack()
    run(checkout(stack.order.addr, "U-01", "k-cur", [(sku_with(4), 1)]))
    latest = rows(stack.order.url, "SELECT max(event_id) FROM order_events")[0][0]
    first = run(collect_events(stack.order.addr, "U-01", after=-1, stop=lambda ev: True, timeout=5, keep_cursor=True))
    assert len(first) == 1 and first[0].order_id == "" and first[0].status == pb.ORDER_STATUS_UNSPECIFIED
    assert first[0].event_id == latest  # nothing is replayed, but the client now knows exactly where "now" is
