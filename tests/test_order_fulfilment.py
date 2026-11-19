"""Fulfilment worker (CONFIRMED -> PACKED -> SHIPPED -> OUT_FOR_DELIVERY -> DELIVERED) and CancelOrder."""
import asyncio
import re
import time
from pathlib import Path

import grpc
import psycopg
import pytest

from shopassist_proto import shopassist_pb2 as pb
from shopassist_proto import shopassist_pb2_grpc as pb_grpc
from tests.conftest import run
from tests.helpers import (FULL_JOURNEY, advance, as_user, checkout, code_of, get_order, order_statuses, rows, sku_with)

SRC = Path(__file__).resolve().parents[1] / "services" / "order"


def cancel(addr, user, order_id, key="cancel-1"):
    with grpc.insecure_channel(addr) as ch:
        return pb_grpc.OrderServiceStub(ch).CancelOrder(pb.CancelOrderRequest(order_id=order_id, idempotency_key=key), metadata=as_user(user))


def stock(url, sku):
    return rows(url, "SELECT on_hand, reserved FROM stock WHERE sku_id = %s", (sku,))[0]


def new_order(stack, key, user="U-01", sku=None, qty=1):
    return run(checkout(stack.order.addr, user, key, [(sku or sku_with(4), qty)]))[-1].order


def wait_until(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condition not reached in time")


# ---------------- fulfilment ----------------

def test_each_pass_moves_a_due_order_exactly_one_step_with_a_human_message(make_stack):
    stack = make_stack()
    order = new_order(stack, "f1")
    messages = []
    for _ in range(4):
        assert advance(stack) == 1
        messages.append(rows(stack.order.url, "SELECT message FROM order_events WHERE order_id = %s ORDER BY event_id DESC LIMIT 1", (order.order_id,))[0][0])
    assert advance(stack) == 0  # delivered orders stay put
    assert order_statuses(stack.order.url, order.order_id) == FULL_JOURNEY
    assert messages == ["Packed at the warehouse", "Handed to the courier", "Out for delivery today", "Delivered. Enjoy!"]
    assert get_order(stack.order.addr, "U-01", order.order_id).status == pb.DELIVERED


def test_an_order_is_not_due_until_the_step_time_has_passed(make_stack):
    stack = make_stack()
    order = new_order(stack, "f2")
    assert advance(stack, step_seconds=3600) == 0
    assert order_statuses(stack.order.url, order.order_id) == ["PENDING", "CONFIRMED"]
    assert advance(stack, step_seconds=0) == 1


def test_pending_and_seeded_history_orders_never_advance(make_stack):
    """Seeded history is finished business (its timestamps are old, so it would otherwise 'be due' at every restart)."""
    stack = make_stack(fault="after_reserve")
    with pytest.raises(grpc.aio.AioRpcError):
        run(checkout(stack.order.addr, "U-02", "f3", [(sku_with(4), 1)]))  # leaves a PENDING order
    before = rows(stack.order.url, "SELECT order_id, status FROM orders ORDER BY order_id")
    assert advance(stack) == 0
    assert rows(stack.order.url, "SELECT order_id, status FROM orders ORDER BY order_id") == before


def test_the_background_worker_walks_an_order_to_delivered(make_stack):
    stack = make_stack(fulfilment=True, fulfilment_step_seconds=0.05, fulfilment_poll_seconds=0.05)
    order = new_order(stack, "f4")
    wait_until(lambda: order_statuses(stack.order.url, order.order_id) == FULL_JOURNEY)
    assert get_order(stack.order.addr, "U-01", order.order_id).status == pb.DELIVERED


# Invariant: however many order-service processes run workers on one database, an order advances ONE step per
# due time: every order's history is the journey exactly once, with no repeated and no skipped status.
def test_two_workers_on_one_database_never_double_advance(make_stack):
    stack = make_stack(fulfilment=True, fulfilment_step_seconds=0.02, fulfilment_poll_seconds=0.01)
    second = start(stack.loop_thread, stack.dbs, "orders-b", stack.create_order, **{**stack.order_opts, "address": "127.0.0.1:0"}) \
        if False else None
    service_b = stack.loop_thread.call(stack.create_order(stack.order.url, **stack.order_opts))  # a second process, same database
    stack.loop_thread.call(service_b.start())
    try:
        orders = [new_order(stack, f"dw{i}", sku=sku_with(4, skip=i)) for i in range(10)]
        wait_until(lambda: all(order_statuses(stack.order.url, o.order_id)[-1] == "DELIVERED" for o in orders), timeout=30)
        for o in orders:
            assert order_statuses(stack.order.url, o.order_id) == FULL_JOURNEY, o.order_id
        assert rows(stack.order.url, "SELECT count(*) FROM order_events WHERE order_id > 'O-2'") == [(10 * len(FULL_JOURNEY),)]
    finally:
        stack.loop_thread.call(service_b.stop(0.1))


# ---------------- cancel ----------------

def test_cancelling_a_confirmed_order_restocks_refunds_and_announces_it(make_stack):
    stack = make_stack()
    sku = sku_with(4)
    order = new_order(stack, "c1", sku=sku, qty=2)
    assert stock(stack.inventory.url, sku) == (2, 0)
    cancelled = cancel(stack.order.addr, "U-01", order.order_id)
    assert cancelled.status == pb.CANCELLED and [h.status for h in cancelled.history][-1] == pb.CANCELLED
    assert stock(stack.inventory.url, sku) == (4, 0)  # the units are back
    assert rows(stack.payment.url, "SELECT status FROM payments WHERE order_id = %s", (order.order_id,)) == [("REFUNDED",)]
    assert order_statuses(stack.order.url, order.order_id) == ["PENDING", "CONFIRMED", "CANCELLED"]
    assert advance(stack) == 0  # a cancelled order is done


def test_a_packed_order_can_be_cancelled_but_a_shipped_one_cannot(make_stack):
    stack = make_stack()
    sku = sku_with(4)
    packed, shipped = new_order(stack, "c2a", sku=sku), new_order(stack, "c2b", sku=sku)
    advance(stack)
    assert cancel(stack.order.addr, "U-01", packed.order_id).status == pb.CANCELLED  # PACKED is fine
    advance(stack)  # c2b: PACKED -> SHIPPED
    with pytest.raises(grpc.RpcError) as e:
        cancel(stack.order.addr, "U-01", shipped.order_id, key="c2b-key")
    assert e.value.code() == grpc.StatusCode.FAILED_PRECONDITION and "SHIPPED" in e.value.details()
    assert get_order(stack.order.addr, "U-01", shipped.order_id).status == pb.SHIPPED
    assert rows(stack.payment.url, "SELECT status FROM payments WHERE order_id = %s", (shipped.order_id,)) == [("SUCCEEDED",)]  # still paid
    assert stock(stack.inventory.url, sku) == (3, 0)  # only the cancelled order's unit came back


def test_cancel_is_idempotent_and_owned(make_stack):
    stack = make_stack()
    sku = sku_with(4)
    order = new_order(stack, "c3", sku=sku)
    first = cancel(stack.order.addr, "U-01", order.order_id, key="same")
    again = cancel(stack.order.addr, "U-01", order.order_id, key="same")
    assert first.status == again.status == pb.CANCELLED and stock(stack.inventory.url, sku) == (4, 0)  # restocked once
    assert rows(stack.payment.url, "SELECT count(*) FROM refunds WHERE idempotency_key = %s", (f"refund:{order.order_id}",)) == [(1,)]
    with pytest.raises(grpc.RpcError) as e:
        cancel(stack.order.addr, "U-01", order.order_id, key="another")  # a different key on an already cancelled order
    assert e.value.code() == grpc.StatusCode.FAILED_PRECONDITION and "CANCELLED" in e.value.details()
    other = new_order(stack, "c3b", sku=sku)
    for user, oid, code in (("U-02", other.order_id, grpc.StatusCode.NOT_FOUND), ("U-01", "O-nope", grpc.StatusCode.NOT_FOUND)):
        with pytest.raises(grpc.RpcError) as e:
            cancel(stack.order.addr, user, oid)
        assert e.value.code() == code
    with grpc.insecure_channel(stack.order.addr) as ch:
        stub = pb_grpc.OrderServiceStub(ch)
        assert code_of(lambda: stub.CancelOrder(pb.CancelOrderRequest(order_id=other.order_id, idempotency_key="k"))) == grpc.StatusCode.UNAUTHENTICATED
        assert code_of(lambda: stub.CancelOrder(pb.CancelOrderRequest(order_id=other.order_id), metadata=as_user("U-01"))) == grpc.StatusCode.INVALID_ARGUMENT


def test_cancelling_a_pending_failed_or_delivered_order_is_refused_with_its_status(make_stack):
    stack = make_stack()
    declined = run(checkout(stack.order.addr, "U-01", "c4", [(sku_with(4), 1)], method="card_declined"))[-1].order
    for order_id, word in ((declined.order_id, "FAILED"), ("O-10001", "DELIVERED")):
        with pytest.raises(grpc.RpcError) as e:
            cancel(stack.order.addr, "U-01", order_id)
        assert e.value.code() == grpc.StatusCode.FAILED_PRECONDITION and word in e.value.details()


# Invariant: when a cancel and the fulfilment worker race for a PACKED order, exactly one wins (a row lock decides):
#   cancel wins  -> CANCELLED, restocked once, refunded once, and no SHIPPED event ever exists
#   fulfilment wins -> SHIPPED, the cancel was refused, nothing restocked, nothing refunded
WORKER_STARTS_AFTER = 0.06


def test_cancel_racing_the_fulfilment_worker_has_exactly_one_winner(make_stack):
    stack = make_stack()
    orders = [new_order(stack, f"race{i}", sku=sku_with(4, skip=i)) for i in range(16)]
    advance(stack)  # all PACKED

    async def cancel_after(order, delay):
        await asyncio.sleep(delay)
        async with grpc.aio.insecure_channel(stack.order.addr) as ch:
            req = pb.CancelOrderRequest(order_id=order.order_id, idempotency_key=f"r-{order.order_id}")
            return await pb_grpc.OrderServiceStub(ch).CancelOrder(req, metadata=as_user("U-01"))

    async def worker():  # fulfilment passes every few ms, so each cancel lands at a different point of the worker's run
        loop = asyncio.get_running_loop()
        await asyncio.sleep(WORKER_STARTS_AFTER)  # tuned so the first cancels are already in flight when the worker begins
        for _ in range(25):
            await loop.run_in_executor(None, advance, stack)
            await asyncio.sleep(0.002)

    async def everything():
        results = await asyncio.gather(*(cancel_after(o, i * 0.006) for i, o in enumerate(orders)), worker(), return_exceptions=True)
        return results[:-1]

    outcomes = run(everything())
    seen = set()
    for order, outcome in zip(orders, outcomes):
        statuses = order_statuses(stack.order.url, order.order_id)
        refunds = rows(stack.payment.url, "SELECT count(*) FROM refunds WHERE idempotency_key = %s", (f"refund:{order.order_id}",))[0][0]
        restocked = rows(stack.inventory.url, "SELECT count(*) FROM restocks WHERE idempotency_key = %s", (f"cancel:{order.order_id}",))[0][0]
        if isinstance(outcome, pb.Order):
            seen.add("cancel")
            assert statuses == ["PENDING", "CONFIRMED", "PACKED", "CANCELLED"] and refunds == restocked == 1, (statuses, refunds)
        else:
            seen.add("advance")
            assert isinstance(outcome, grpc.aio.AioRpcError) and outcome.code() == grpc.StatusCode.FAILED_PRECONDITION, outcome
            assert "CANCELLED" not in statuses and "SHIPPED" in statuses and refunds == restocked == 0, (statuses, refunds)
    print(f"CANCEL/ADVANCE RACE outcomes over {len(orders)} orders: {sorted(seen)}")


def test_a_crash_between_restock_and_refund_is_finished_by_recovery(make_stack):
    stack = make_stack(fault="cancel_after_restock")
    sku = sku_with(4)
    order = new_order(stack, "c5", sku=sku, qty=2)  # the fault only hooks cancellation, so checkout is unaffected
    with pytest.raises(grpc.RpcError) as e:
        cancel(stack.order.addr, "U-01", order.order_id, key="crash")
    assert e.value.code() == grpc.StatusCode.UNAVAILABLE
    assert stock(stack.inventory.url, sku) == (4, 0)  # restocked...
    assert rows(stack.payment.url, "SELECT status FROM payments WHERE order_id = %s", (order.order_id,)) == [("SUCCEEDED",)]  # ...not yet refunded
    assert get_order(stack.order.addr, "U-01", order.order_id).status == pb.CONFIRMED

    stack.restart_order(fault=None, recovery_min_age_seconds=0)
    wait_until(lambda: (stack.loop_thread.call(stack.order.service.recover_now()), get_order(stack.order.addr, "U-01", order.order_id).status)[1] == pb.CANCELLED)
    assert stock(stack.inventory.url, sku) == (4, 0)  # restocked exactly once, not twice
    assert rows(stack.payment.url, "SELECT status FROM payments WHERE order_id = %s", (order.order_id,)) == [("REFUNDED",)]
    assert order_statuses(stack.order.url, order.order_id) == ["PENDING", "CONFIRMED", "CANCELLED"]
    assert stack.loop_thread.call(stack.order.service.recover_now()) == 0


def test_a_cancel_in_progress_is_not_advanced_by_the_worker(make_stack):
    stack = make_stack(fault="cancel_after_restock")
    order = new_order(stack, "c6")
    with pytest.raises(grpc.RpcError):
        cancel(stack.order.addr, "U-01", order.order_id, key="stuck")
    assert advance(stack) == 0  # claimed by the cancellation: the worker leaves it alone
    assert order_statuses(stack.order.url, order.order_id) == ["PENDING", "CONFIRMED"]
