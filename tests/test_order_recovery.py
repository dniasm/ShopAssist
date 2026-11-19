"""Crashes in the middle of a checkout saga, and how recovery resolves them.

`ORDER_FAULT` (the `fault` option here) makes the saga raise right AFTER a remote call returned and BEFORE its
outcome was saved: the hardest moment, because the outside world has changed and the order row does not know yet.
Recovery then re-issues the recorded step with the same idempotency key to learn what happened.
"""
import time

import grpc
import pytest

from shopassist_proto import shopassist_pb2 as pb
from tests.conftest import run
from tests.helpers import checkout, get_order, rows, sku_with, steps


def stock(url, sku):
    return rows(url, "SELECT on_hand, reserved FROM stock WHERE sku_id = %s", (sku,))[0]


def new_payments(url):
    return rows(url, "SELECT status, amount_cents FROM payments WHERE payment_id > 'PAY-2'")


def crashed_order_id(stack, user="U-01"):
    ids = rows(stack.order.url, "SELECT order_id FROM orders WHERE user_id = %s AND order_id > 'O-2' ORDER BY created_at", (user,))
    assert len(ids) == 1, ids
    return ids[0][0]


def settle(stack, user, order_id, timeout=10):
    """Run recovery and wait until the order leaves PENDING (startup recovery may be doing the same work, which is fine)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        stack.loop_thread.call(stack.order.service.recover_now())
        order = get_order(stack.order.addr, user, order_id)
        if order.status != pb.PENDING:
            return order
        time.sleep(0.1)
    raise AssertionError("order still PENDING after recovery")


def test_crash_after_the_charge_is_rolled_forward_to_confirmed(make_stack):
    stack = make_stack(fault="after_charge")
    sku = sku_with(4)
    with pytest.raises(grpc.aio.AioRpcError) as e:
        run(checkout(stack.order.addr, "U-01", "crash-1", [(sku, 2)]))
    assert e.value.code() == grpc.StatusCode.UNAVAILABLE  # "interrupted: retry with the same key"
    order_id = crashed_order_id(stack)
    # the world as the crash left it: paid, stock held, order unaware
    assert get_order(stack.order.addr, "U-01", order_id).status == pb.PENDING
    assert [p[0] for p in new_payments(stack.payment.url)] == ["SUCCEEDED"]
    assert rows(stack.inventory.url, "SELECT state FROM reservations") == [("HELD",)]

    stack.restart_order(fault=None, recovery_min_age_seconds=0)
    order = settle(stack, "U-01", order_id)

    assert order.status == pb.CONFIRMED and not order.failure_reason  # the customer paid, so the order goes ahead
    assert order.payment_id.startswith("PAY-")
    assert stock(stack.inventory.url, sku) == (2, 0)  # committed
    assert [p[0] for p in new_payments(stack.payment.url)] == ["SUCCEEDED"]  # still exactly one charge
    assert rows(stack.inventory.url, "SELECT state FROM reservations") == [("COMMITTED",)]
    assert [h.status for h in order.history] == [pb.PENDING, pb.CONFIRMED]
    # the client retrying its key now simply gets the settled order
    again = run(checkout(stack.order.addr, "U-01", "crash-1", [(sku, 2)]))
    assert steps(again) == [("FINISHED", "SUCCEEDED")] and again[-1].order.order_id == order_id


def test_crash_after_reserve_is_compensated_with_no_charge(make_stack):
    stack = make_stack(fault="after_reserve")
    sku = sku_with(4)
    with pytest.raises(grpc.aio.AioRpcError):
        run(checkout(stack.order.addr, "U-01", "crash-2", [(sku, 2)]))
    order_id = crashed_order_id(stack)
    assert stock(stack.inventory.url, sku) == (4, 2)  # held by the dead saga

    stack.restart_order(fault=None, recovery_min_age_seconds=0)
    order = settle(stack, "U-01", order_id)

    assert order.status == pb.FAILED and order.failure_reason == "recovered after interruption"
    assert rows(stack.inventory.url, "SELECT state FROM reservations") == [("RELEASED",)]
    assert stock(stack.inventory.url, sku) == (4, 0)
    assert new_payments(stack.payment.url) == []
    assert [h.status for h in order.history] == [pb.PENDING, pb.FAILED]


def test_crash_after_a_declined_charge_is_compensated_too(make_stack):
    stack = make_stack(fault="after_charge")
    sku = sku_with(4)
    with pytest.raises(grpc.aio.AioRpcError):
        run(checkout(stack.order.addr, "U-01", "crash-3", [(sku, 1)], method="card_declined"))
    order_id = crashed_order_id(stack)
    stack.restart_order(fault=None, recovery_min_age_seconds=0)
    order = settle(stack, "U-01", order_id)
    assert order.status == pb.FAILED and stock(stack.inventory.url, sku) == (4, 0)
    assert [p[0] for p in new_payments(stack.payment.url)] == ["DECLINED"]  # same key -> the same decline, nothing new
    assert rows(stack.inventory.url, "SELECT state FROM reservations") == [("RELEASED",)]


def test_recovery_leaves_young_orders_alone(make_stack):
    stack = make_stack(fault="after_reserve")  # default: only orders older than 60 s are recovered
    with pytest.raises(grpc.aio.AioRpcError):
        run(checkout(stack.order.addr, "U-01", "crash-4", [(sku_with(4), 1)]))
    stack.restart_order(fault=None)
    assert stack.loop_thread.call(stack.order.service.recover_now()) == 0
    assert get_order(stack.order.addr, "U-01", crashed_order_id(stack)).status == pb.PENDING


def test_recovery_twice_changes_nothing_the_second_time(make_stack):
    stack = make_stack(fault="after_charge")
    sku = sku_with(4)
    with pytest.raises(grpc.aio.AioRpcError):
        run(checkout(stack.order.addr, "U-01", "crash-5", [(sku, 1)]))
    order_id = crashed_order_id(stack)
    stack.restart_order(fault=None, recovery_min_age_seconds=0)
    settle(stack, "U-01", order_id)
    assert stack.loop_thread.call(stack.order.service.recover_now()) == 0
    assert len(new_payments(stack.payment.url)) == 1 and stock(stack.inventory.url, sku) == (3, 0)
    assert rows(stack.order.url, "SELECT count(*) FROM order_events WHERE order_id = %s", (order_id,)) == [(2,)]


def test_a_hold_that_expired_before_commit_is_refunded_and_the_order_fails(make_stack):
    stack = make_stack(payment_latency_ms=1500, reservation_ttl_seconds=1)  # the card takes longer than the hold lasts
    sku = sku_with(4)
    events = run(checkout(stack.order.addr, "U-01", "slow", [(sku, 2)]))
    assert steps(events)[-5:] == [("CONFIRMING", "STARTED"), ("CONFIRMING", "FAILED"), ("COMPENSATING", "STARTED"),
                                  ("COMPENSATING", "SUCCEEDED"), ("FINISHED", "FAILED")]
    order = events[-1].order
    assert order.status == pb.FAILED and "expired" in order.failure_reason
    assert rows(stack.payment.url, "SELECT status FROM payments WHERE order_id = %s", (order.order_id,)) == [("REFUNDED",)]
    assert rows(stack.payment.url, "SELECT count(*) FROM refunds WHERE idempotency_key = %s", (f"refund:{order.order_id}",)) == [(1,)]
    assert rows(stack.inventory.url, "SELECT state FROM reservations") == [("EXPIRED",)]
    assert stock(stack.inventory.url, sku) == (4, 0)


# Invariant: recovery only ever LEARNS what happened to a charge (LookupCharge); it never starts one.
# `before_charge` crashes after the write-ahead CHARGING step but BEFORE the request reaches Payment, so a
# recovery that re-issued Charge would bill the customer for the first time a minute after the checkout died.
def test_crash_before_the_charge_reaches_payment_is_compensated_and_nobody_is_ever_charged(make_stack):
    stack = make_stack(fault="before_charge")
    sku = sku_with(4)
    with pytest.raises(grpc.aio.AioRpcError):
        run(checkout(stack.order.addr, "U-01", "crash-6", [(sku, 2)]))
    order_id = crashed_order_id(stack)
    assert new_payments(stack.payment.url) == [] and stock(stack.inventory.url, sku) == (4, 2)

    stack.restart_order(fault=None, recovery_min_age_seconds=0)
    order = settle(stack, "U-01", order_id)

    assert order.status == pb.FAILED and order.failure_reason == "recovered after interruption"
    assert new_payments(stack.payment.url) == []  # no charge, then or ever
    assert rows(stack.payment.url, "SELECT count(*) FROM refunds") == [(1,)]  # only the seeded refund
    assert rows(stack.inventory.url, "SELECT state FROM reservations") == [("RELEASED",)]
    assert stock(stack.inventory.url, sku) == (4, 0)
    stack.loop_thread.call(stack.order.service.recover_now())
    assert new_payments(stack.payment.url) == []  # and a later pass does not either
