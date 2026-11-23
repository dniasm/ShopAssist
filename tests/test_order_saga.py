"""OrderService checkout saga against real Inventory, Payment and Catalog services and real Postgres databases.

Every test here is about an invariant of checkout: money moves once, stock never goes negative, a failed
checkout leaves nothing held, and a repeated key never creates a second order.
"""
import grpc
import pytest

from tests import authchan
from shopassist_proto import shopassist_pb2 as pb
from shopassist_proto import shopassist_pb2_grpc as pb_grpc
from tests.conftest import run
from tests.fixtures import PRODUCTS, SEED_ORDERS, SKUS
from tests.helpers import (SKU_PRICE, as_user, checkout, checkout_many, checkout_request, cheapest_sku_with, code_of,
                           get_order, rows, sku_with, steps)

HAPPY = [
    ("VALIDATING", "STARTED"), ("VALIDATING", "SUCCEEDED"), ("RESERVING", "STARTED"), ("RESERVING", "SUCCEEDED"),
    ("CHARGING", "STARTED"), ("CHARGING", "SUCCEEDED"), ("CONFIRMING", "STARTED"), ("CONFIRMING", "SUCCEEDED"),
    ("FINISHED", "SUCCEEDED"),
]


def stock(url, sku):
    return rows(url, "SELECT on_hand, reserved FROM stock WHERE sku_id = %s", (sku,))[0]


def payments(url, order_id=None):
    sql = "SELECT status, amount_cents FROM payments" + (" WHERE order_id = %s" if order_id else " WHERE payment_id > 'PAY-2'")
    return rows(url, sql, (order_id,) if order_id else ())


# ---------------- happy path ----------------

def test_checkout_streams_every_step_then_a_confirmed_order(make_stack):
    stack = make_stack()
    sku = sku_with(4)
    events = run(checkout(stack.order.addr, "U-01", "k-1", [(sku, 2)]))
    assert steps(events) == HAPPY
    order = events[-1].order
    assert order.status == pb.CONFIRMED and order.user_id == "U-01" and order.order_id.startswith("O-2")
    assert {e.order_id for e in events} == {order.order_id}
    assert [(l.sku_id, l.quantity, l.unit_price_cents) for l in order.lines] == [(sku, 2, SKU_PRICE[sku])]
    assert order.subtotal_cents == 2 * SKU_PRICE[sku] and order.total_cents == order.subtotal_cents + order.shipping_cents
    assert order.payment_id.startswith("PAY-") and not order.failure_reason
    assert stock(stack.inventory.url, sku) == (2, 0)  # committed: left on_hand, nothing still held
    assert payments(stack.payment.url, order.order_id) == [("SUCCEEDED", order.total_cents)]
    assert [h.status for h in order.history] == [pb.PENDING, pb.CONFIRMED]


def test_shipping_is_499_under_50_dollars_and_free_from_50(make_stack):
    stack = make_stack()
    sku = cheapest_sku_with(4)
    price = SKU_PRICE[sku]
    small = run(checkout(stack.order.addr, "U-01", "small", [(sku, 1)]))[-1].order
    big = run(checkout(stack.order.addr, "U-01", "big", [(sku, 3)]))[-1].order
    assert price < 5000 and small.shipping_cents == 499
    assert (big.shipping_cents == 0) == (3 * price >= 5000)


def test_prices_come_from_the_catalog_not_from_the_caller(make_stack):
    stack = make_stack()
    sku = sku_with(3)
    order = run(checkout(stack.order.addr, "U-01", "k-p", [(sku, 1)]))[-1].order
    product = next(p for p in PRODUCTS if p["price_cents"] == SKU_PRICE[sku])
    assert order.lines[0].unit_price_cents == product["price_cents"] and order.lines[0].name


def test_order_events_are_written_with_every_status_change(make_stack):
    stack = make_stack()
    order = run(checkout(stack.order.addr, "U-01", "k-e", [(sku_with(4), 1)]))[-1].order
    events = rows(stack.order.url, "SELECT status, user_id FROM order_events WHERE order_id = %s ORDER BY event_id", (order.order_id,))
    assert events == [("PENDING", "U-01"), ("CONFIRMED", "U-01")]


# ---------------- failures leave nothing behind ----------------

def test_unknown_sku_fails_validation_without_touching_stock_or_money(make_stack):
    stack = make_stack()
    events = run(checkout(stack.order.addr, "U-01", "k-u", [("SKU-nope", 1)]))
    assert steps(events)[-2:] == [("VALIDATING", "FAILED"), ("FINISHED", "FAILED")]
    order = events[-1].order
    assert order.status == pb.FAILED and "SKU-nope" in order.failure_reason
    assert payments(stack.payment.url) == [] and rows(stack.inventory.url, "SELECT count(*) FROM reservations") == [(0,)]


def test_bad_requests_are_invalid_argument(make_stack):
    stack = make_stack()
    with authchan.channel(stack.order.addr) as ch:
        stub = pb_grpc.OrderServiceStub(ch)
        for req in (checkout_request("k", []), checkout_request("", [(sku_with(4), 1)]), checkout_request("k", [(sku_with(4), 0)]),
                    checkout_request("k", [(sku_with(4), 1)], method="")):
            assert code_of(lambda: list(stub.Checkout(req, metadata=as_user("U-01")))) == grpc.StatusCode.INVALID_ARGUMENT
        assert code_of(lambda: list(stub.Checkout(checkout_request("k", [(sku_with(4), 1)])))) == grpc.StatusCode.UNAUTHENTICATED


def test_not_enough_stock_fails_with_shortages_and_holds_nothing(make_stack):
    stack = make_stack()
    scarce, plenty = sku_with(1), sku_with(4)
    events = run(checkout(stack.order.addr, "U-01", "k-s", [(plenty, 1), (scarce, 3)]))
    assert steps(events)[-3:] == [("RESERVING", "STARTED"), ("RESERVING", "FAILED"), ("FINISHED", "FAILED")]
    failed = next(e for e in events if e.step == pb.RESERVING and e.state == pb.STEP_FAILED)
    assert [(s.sku_id, s.requested, s.available) for s in failed.shortages] == [(scarce, 3, 1)]
    assert [(s.sku_id, s.requested, s.available) for s in events[-1].shortages] == [(scarce, 3, 1)]
    assert stock(stack.inventory.url, plenty) == (4, 0) and stock(stack.inventory.url, scarce) == (1, 0)
    assert payments(stack.payment.url) == []


def test_declined_card_releases_the_reservation_and_charges_nothing(make_stack):
    stack = make_stack()
    sku = sku_with(4)
    events = run(checkout(stack.order.addr, "U-01", "k-d", [(sku, 2)], method="card_declined"))
    assert steps(events)[-5:] == [("CHARGING", "STARTED"), ("CHARGING", "FAILED"), ("COMPENSATING", "STARTED"),
                                  ("COMPENSATING", "SUCCEEDED"), ("FINISHED", "FAILED")]
    order = events[-1].order
    assert order.status == pb.FAILED and "declined" in order.failure_reason
    assert rows(stack.inventory.url, "SELECT state FROM reservations WHERE reservation_id = %s", (order.order_id,)) == [("RELEASED",)]
    assert stock(stack.inventory.url, sku) == (4, 0)  # available is back to what it was
    assert payments(stack.payment.url, order.order_id) == [("DECLINED", order.total_cents)]  # the attempt is recorded, no money moved
    assert not order.payment_id
    assert [h.status for h in order.history] == [pb.PENDING, pb.FAILED]


# ---------------- idempotency ----------------

def test_same_key_after_completion_replays_the_final_event_and_changes_nothing(make_stack):
    stack = make_stack()
    sku = sku_with(4)
    first = run(checkout(stack.order.addr, "U-01", "k-r", [(sku, 1)]))
    again = run(checkout(stack.order.addr, "U-01", "k-r", [(sku, 1)]))
    assert steps(again) == [("FINISHED", "SUCCEEDED")] and again[-1].order.order_id == first[-1].order.order_id
    assert stock(stack.inventory.url, sku) == (3, 0) and len(payments(stack.payment.url)) == 1


def test_replaying_a_failed_checkout_stays_failed_and_does_not_retry_the_card(make_stack):
    stack = make_stack()
    first = run(checkout(stack.order.addr, "U-01", "k-f", [(sku_with(4), 1)], method="card_declined"))
    again = run(checkout(stack.order.addr, "U-01", "k-f", [(sku_with(4), 1)], method="card_declined"))
    assert again[-1].order.order_id == first[-1].order.order_id and again[-1].order.status == pb.FAILED
    assert len(rows(stack.payment.url, "SELECT 1 FROM payments WHERE order_id = %s", (first[-1].order.order_id,))) == 1


def test_a_reused_key_with_a_different_cart_is_already_exists(make_stack):
    stack = make_stack()
    a, b = sku_with(4), sku_with(3)
    run(checkout(stack.order.addr, "U-01", "k-x", [(a, 1)]))
    for lines, method in (([(a, 2)], "card_ok"), ([(b, 1)], "card_ok"), ([(a, 1)], "card_declined")):
        with pytest.raises(grpc.aio.AioRpcError) as e:
            run(checkout(stack.order.addr, "U-01", "k-x", lines, method))
        assert e.value.code() == grpc.StatusCode.ALREADY_EXISTS
    assert stock(stack.inventory.url, a) == (3, 0)  # nothing extra happened
    assert len(payments(stack.payment.url)) == 1


def test_keys_are_per_user(make_stack):
    stack = make_stack()
    sku = sku_with(4)
    one = run(checkout(stack.order.addr, "U-01", "same-key", [(sku, 1)]))[-1].order
    two = run(checkout(stack.order.addr, "U-02", "same-key", [(sku, 1)]))[-1].order
    assert one.order_id != two.order_id and (one.user_id, two.user_id) == ("U-01", "U-02")


# Invariant: the same (user, key) submitted twice at the same time is ONE order, ONE reservation, ONE charge,
# and both callers get the same order id and the same final result. (The double-click.)
def test_double_submit_is_one_order_one_charge(make_stack):
    stack = make_stack(payment_latency_ms=200)  # the charge is slow, so the second call arrives mid-saga
    sku = sku_with(4)
    results = run(checkout_many(stack.order.addr, [("U-01", "dbl", [(sku, 2)])] * 2))
    assert all(isinstance(r, list) for r in results), results
    ids = {r[-1].order.order_id for r in results}
    assert len(ids) == 1 and all(r[-1].order.status == pb.CONFIRMED for r in results)
    assert len(payments(stack.payment.url)) == 1 and rows(stack.inventory.url, "SELECT count(*) FROM reservations") == [(1,)]
    assert stock(stack.inventory.url, sku) == (2, 0)
    assert rows(stack.order.url, "SELECT count(*) FROM orders WHERE user_id = 'U-01' AND order_id > 'O-2'") == [(1,)]


# Invariants, with 1 unit in stock and 20 different customers checking out at once:
#   exactly 1 order CONFIRMED and 19 FAILED with a shortage; stock ends on_hand 0 / reserved 0 (never negative);
#   exactly ONE payment row exists (the 19 losers never reach the card).
def test_twenty_concurrent_checkouts_for_one_unit_confirm_exactly_one(make_stack):
    stack = make_stack(payment_latency_ms=50)
    sku = sku_with(1)
    results = run(checkout_many(stack.order.addr, [(f"U-R{i:02d}", f"race-{i}", [(sku, 1)], "card_ok") for i in range(20)]))
    assert all(isinstance(r, list) for r in results), [r for r in results if not isinstance(r, list)]
    finals = [r[-1].order for r in results]
    confirmed = [o for o in finals if o.status == pb.CONFIRMED]
    failed = [o for o in finals if o.status == pb.FAILED]
    shortages = [r[-1].shortages for r in results if r[-1].order.status == pb.FAILED]
    print(f"\nRACE RESULT: confirmed={len(confirmed)} failed={len(failed)} failed_with_shortage={sum(1 for s in shortages if s)} "
          f"stock(on_hand, reserved)={stock(stack.inventory.url, sku)} payment_rows={len(payments(stack.payment.url))}")
    assert (len(confirmed), len(failed)) == (1, 19)
    assert all(s and s[0].sku_id == sku for s in shortages)
    assert stock(stack.inventory.url, sku) == (0, 0)
    assert rows(stack.inventory.url, "SELECT count(*) FROM stock WHERE reserved < 0 OR on_hand - reserved < 0") == [(0,)]
    assert payments(stack.payment.url) == [("SUCCEEDED", confirmed[0].total_cents)]


# ---------------- reading orders ----------------

def test_users_can_only_read_their_own_orders(make_stack):
    stack = make_stack()
    order = run(checkout(stack.order.addr, "U-01", "k-o", [(sku_with(4), 1)]))[-1].order
    assert get_order(stack.order.addr, "U-01", order.order_id).order_id == order.order_id
    with pytest.raises(grpc.RpcError) as e:
        get_order(stack.order.addr, "U-02", order.order_id)  # not "forbidden": we do not reveal that it exists
    assert e.value.code() == grpc.StatusCode.NOT_FOUND
    with pytest.raises(grpc.RpcError) as e:
        get_order(stack.order.addr, "U-01", "O-nope")
    assert e.value.code() == grpc.StatusCode.NOT_FOUND
    with authchan.channel(stack.order.addr) as ch:
        stub = pb_grpc.OrderServiceStub(ch)
        assert code_of(lambda: stub.GetOrder(pb.GetOrderRequest(order_id=order.order_id))) == grpc.StatusCode.UNAUTHENTICATED
        assert not stub.ListOrders(pb.ListOrdersRequest(), metadata=as_user("U-03")).orders


def test_list_orders_is_newest_first_paged_and_scoped_to_the_user(make_stack):
    stack = make_stack()
    made = [run(checkout(stack.order.addr, "U-01", f"k-l{i}", [(sku_with(4), 1)]))[-1].order.order_id for i in range(3)]
    run(checkout(stack.order.addr, "U-02", "k-other", [(sku_with(4), 1)]))
    with authchan.channel(stack.order.addr) as ch:
        stub = pb_grpc.OrderServiceStub(ch)
        page1 = stub.ListOrders(pb.ListOrdersRequest(page_size=2), metadata=as_user("U-01"))
        page2 = stub.ListOrders(pb.ListOrdersRequest(page_size=2, page_token=page1.next_page_token), metadata=as_user("U-01"))
    got = [o.order_id for o in page1.orders] + [o.order_id for o in page2.orders]
    assert got == [*reversed(made), "O-10001"]
    assert page1.next_page_token and not page2.next_page_token
    assert all(not o.history for o in page1.orders)  # lists are light; GetOrder carries the history


def test_historic_orders_are_seeded_with_their_events_and_new_ids_do_not_collide(make_stack):
    stack = make_stack()
    seeded = get_order(stack.order.addr, "U-02", "O-10002")
    assert seeded.status == pb.CANCELLED and [h.status for h in seeded.history] == [pb.CONFIRMED, pb.CANCELLED]
    assert seeded.payment_id == "PAY-10002"  # linked through payments.json
    fresh = run(checkout(stack.order.addr, "U-02", "k-n", [(sku_with(4), 1)]))[-1].order
    assert fresh.order_id not in {o["order_id"] for o in SEED_ORDERS}
    ids = rows(stack.order.url, "SELECT event_id FROM order_events ORDER BY event_id")
    assert [i[0] for i in ids] == sorted(i[0] for i in ids) and len(set(ids)) == len(ids)


# ---------------- retrying a finished checkout whose cart is already empty ----------------

def test_same_key_with_no_lines_replays_the_existing_checkout(make_stack):
    stack = make_stack()
    sku = sku_with(4)
    first = run(checkout(stack.order.addr, "U-01", "k-nl", [(sku, 1)]))[-1].order
    again = run(checkout(stack.order.addr, "U-01", "k-nl", []))  # the client's cart was cleared by the first run
    assert steps(again) == [("FINISHED", "SUCCEEDED")] and again[-1].order.order_id == first.order_id
    assert stock(stack.inventory.url, sku) == (3, 0) and len(payments(stack.payment.url)) == 1


def test_no_lines_and_an_unknown_key_is_still_invalid(make_stack):
    stack = make_stack()
    with pytest.raises(grpc.aio.AioRpcError) as e:
        run(checkout(stack.order.addr, "U-01", "never-used", []))
    assert e.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    with pytest.raises(grpc.aio.AioRpcError) as e:  # another user's key is not this user's key
        run(checkout(stack.order.addr, "U-01", "k-other", [(sku_with(4), 1)]))
        run(checkout(stack.order.addr, "U-02", "k-other", []))
    assert e.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert rows(stack.order.url, "SELECT count(*) FROM orders WHERE order_id > 'O-2'") == [(1,)]  # nothing was created by the empty calls


# ---------------- category snapshot on order lines ----------------

def test_order_lines_snapshot_the_category_at_checkout(make_stack):
    stack = make_stack()
    sku = sku_with(4)
    order = run(checkout(stack.order.addr, "U-01", "k-cat", [(sku, 1)]))[-1].order
    expected = next(p["category"] for p in PRODUCTS if p["product_id"] == next(s["product_id"] for s in SKUS if s["sku_id"] == sku))
    assert [l.category for l in order.lines] == [expected]
    assert [l.category for l in get_order(stack.order.addr, "U-01", order.order_id).lines] == [expected]


def test_seeded_history_gets_its_category_from_the_catalog_seed_and_a_restart_backfills_gaps(make_stack, seed_dir):
    stack = make_stack()
    seeded = get_order(stack.order.addr, "U-01", "O-10001")
    expected = next(p["category"] for p in PRODUCTS if p["product_id"] == "P-1001")
    assert [l.category for l in seeded.lines] == [expected]
    # a database seeded before this column existed: lines without a category get one on the next start
    import psycopg
    with psycopg.connect(stack.order.url) as conn:
        conn.execute("UPDATE order_lines SET category = NULL")
    stack.restart_order()
    assert [l.category for l in get_order(stack.order.addr, "U-01", "O-10001").lines] == [expected]
