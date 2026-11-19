"""Order tracking through the API: order events on the session WebSocket, cancel, and watcher lifecycle."""
import time

import pytest

from tests.helpers import advance, rows, sku_with
from tests.test_api_shop import error, make_shop, valid  # noqa: F401  (fixtures and helpers shared with the shop tests)


def next_status(ws, order_id, status, timeout=15):
    """Read until `order/status` for this order with this status arrives; returns all messages read."""
    seen, deadline = [], time.monotonic() + timeout
    while time.monotonic() < deadline:
        msg = ws.get(max(0.1, deadline - time.monotonic()))
        seen.append(msg)
        if isinstance(msg, dict) and msg["channel"] == "order" and msg["data"]["order_id"] == order_id and msg["data"]["status"] == status:
            return seen
    raise AssertionError(f"no {status} for {order_id}; saw {[(m['channel'], m['type'], m['data'].get('status')) for m in seen if isinstance(m, dict)]}")


def statuses(messages, order_id):
    return [m["data"]["status"] for m in messages if isinstance(m, dict) and m["channel"] == "order" and m["data"]["order_id"] == order_id]


def place(shop, headers, key, sku=None):
    shop.put(headers, sku or sku_with(4), 1)
    return shop.checkout(headers, key).json()["order_id"]


def test_status_changes_reach_the_websocket_on_the_order_channel(make_shop):
    shop = make_shop(order_watch_stop_seconds=0.3)
    headers, token = shop.login("U-03")
    ws = shop.ws(token)
    ws.get()
    order_id = place(shop, headers, "t1")
    seen = next_status(ws, order_id, "CONFIRMED")
    for _ in range(4):
        advance(shop.stack)
    seen += next_status(ws, order_id, "DELIVERED")
    assert statuses(seen, order_id) == ["PENDING", "CONFIRMED", "PACKED", "SHIPPED", "OUT_FOR_DELIVERY", "DELIVERED"]
    event = next(m for m in seen if m["channel"] == "order" and m["data"]["status"] == "SHIPPED")
    valid(event)
    assert set(event["data"]) == {"order_id", "status", "message", "event_id"} and event["data"]["message"] == "Handed to the courier"
    ids = [m["data"]["event_id"] for m in seen if m["channel"] == "order"]
    assert ids == sorted(set(ids))  # no duplicates, in order
    seqs = [m["seq"] for m in seen]
    assert seqs == sorted(set(seqs))  # one per-user sequence shared by both channels


def test_one_watch_stream_per_user_however_many_tabs_and_it_stops_after_the_last_closes(make_shop):
    shop = make_shop(order_watch_stop_seconds=0.4)
    _, token_a = shop.login("U-04")
    _, token_b = shop.login("U-05")
    watch = lambda: shop.stack.loop_thread.call(_count(shop))  # noqa: E731

    async def _count(shop):
        return shop.stack.order.service.watch_subscribers()

    tabs = [shop.ws(token_a), shop.ws(token_a), shop.ws(token_a)]
    for tab in tabs:
        tab.get()
    other = shop.ws(token_b)
    other.get()
    deadline = time.monotonic() + 5
    while watch() != 2 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert watch() == 2  # one stream for user A (three tabs) and one for user B
    for tab in tabs:
        tab.close()
    time.sleep(0.1)
    assert watch() == 2  # not stopped immediately: a page refresh must not churn the stream
    deadline = time.monotonic() + 5
    while watch() != 1 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert watch() == 1  # A's stream stopped after the grace period; B's is still there


def test_a_dropped_watch_stream_resumes_without_a_gap_or_a_repeat(make_shop):
    shop = make_shop(order_watch_stop_seconds=30, order_watch_retry_seconds=0.2)
    headers, token = shop.login("U-06")
    ws = shop.ws(token)
    ws.get()
    order_id = place(shop, headers, "t3")
    seen = next_status(ws, order_id, "CONFIRMED")
    port = shop.stack.order.addr.rsplit(":", 1)[1]
    shop.stack.restart_order(address=f"127.0.0.1:{port}", recovery_min_age_seconds=60)  # every WatchOrders stream is cut
    advance(shop.stack)  # PACKED happens while the orchestrator is reconnecting
    seen += next_status(ws, order_id, "PACKED")
    advance(shop.stack)  # SHIPPED arrives on the new stream
    seen += next_status(ws, order_id, "SHIPPED")
    assert statuses(seen, order_id) == ["PENDING", "CONFIRMED", "PACKED", "SHIPPED"]  # nothing missing, nothing twice


def test_a_user_with_no_open_socket_costs_nothing_and_misses_nothing_when_they_return(make_shop):
    shop = make_shop(order_watch_stop_seconds=0.2)
    headers, token = shop.login("U-07")
    first = shop.ws(token)
    first.get()
    order_id = place(shop, headers, "t4")
    next_status(first, order_id, "CONFIRMED")
    first.close()
    time.sleep(0.8)  # the watch stream is stopped now
    advance(shop.stack)  # PACKED while nobody is connected
    back = shop.ws(token, after=0)  # a returning tab replays what the log kept, and the watcher resumes from its cursor
    back.get()
    assert "PACKED" in statuses(next_status(back, order_id, "PACKED"), order_id)


def test_cancel_endpoint_cancels_announces_and_refuses_what_cannot_be_cancelled(make_shop):
    shop = make_shop(order_watch_stop_seconds=0.3)
    headers, token = shop.login("U-08")
    other, _ = shop.login("U-09")
    ws = shop.ws(token)
    ws.get()
    cancellable, shipped = place(shop, headers, "t5a"), place(shop, headers, "t5b")
    next_status(ws, shipped, "CONFIRMED")
    r = shop.client.post(f"/api/orders/{cancellable}/cancel", json={"idempotency_key": "cx-1"}, headers=headers)
    assert r.status_code == 200 and r.json()["order"]["status"] == "CANCELLED"
    assert [h["status"] for h in r.json()["order"]["history"]][-1] == "CANCELLED"
    assert "CANCELLED" in statuses(next_status(ws, cancellable, "CANCELLED"), cancellable)
    again = shop.client.post(f"/api/orders/{cancellable}/cancel", json={"idempotency_key": "cx-1"}, headers=headers)
    assert again.status_code == 200 and again.json()["order"]["status"] == "CANCELLED"  # a retry is fine
    for _ in range(2):
        advance(shop.stack)  # the other order: CONFIRMED -> PACKED -> SHIPPED
    err = error(shop.client.post(f"/api/orders/{shipped}/cancel", json={"idempotency_key": "cx-2"}, headers=headers), 409, "conflict")
    assert "SHIPPED" in err["message"]
    error(shop.client.post(f"/api/orders/{shipped}/cancel", json={"idempotency_key": "cx-3"}, headers=other), 404, "not_found")
    error(shop.client.post(f"/api/orders/{shipped}/cancel", json={}, headers=headers), 422, "invalid")
    error(shop.client.post(f"/api/orders/{shipped}/cancel", json={"idempotency_key": "k"}), 401, "unauthenticated")


def test_browsing_orders_through_the_api_never_writes(make_shop):
    shop = make_shop()
    headers, token = shop.login("U-10")
    ws = shop.ws(token)
    ws.get()
    order_id = place(shop, headers, "t6")
    ws.until("done")
    snapshot = lambda: rows(shop.stack.order.url, "SELECT count(*), max(event_id) FROM order_events")  # noqa: E731
    before = snapshot()
    for _ in range(30):
        shop.client.get("/api/orders", headers=headers)
        shop.client.get(f"/api/orders/{order_id}", headers=headers)
        shop.client.get("/api/cart", headers=headers)
    assert snapshot() == before


# Invariant: events can COMMIT in a different order than their ids (ids are handed out at insert, commits race).
# If the watcher saw id 12 and an earlier-numbered id 11 commits while the stream is down, resuming "after 12" would lose
# 11 for good and a page could sit on SHIPPED while the order was DELIVERED. Resuming from a little before the last
# seen id, and dropping what was already forwarded, delivers every event exactly once.
def test_events_committed_out_of_id_order_across_a_reconnect_each_arrive_exactly_once(make_shop):
    import psycopg
    shop = make_shop(order_watch_stop_seconds=30, order_watch_retry_seconds=0.2)
    headers, token = shop.login("U-03")
    ws = shop.ws(token)
    ws.get()
    order_id = place(shop, headers, "oo1")
    seen = next_status(ws, order_id, "CONFIRMED")
    insert = "INSERT INTO order_events (order_id, user_id, status, message) VALUES (%s, 'U-03', %s, 'x') RETURNING event_id"
    first, second = psycopg.connect(shop.stack.order.url), psycopg.connect(shop.stack.order.url)
    try:
        lower = first.execute(insert, (order_id, "PACKED")).fetchone()[0]  # id N, not committed yet
        higher = second.execute(insert, (order_id, "SHIPPED")).fetchone()[0]  # id N+1 ...
        second.execute("SELECT pg_notify('order_events', %s)", (str(higher),))
        second.commit()  # ... commits FIRST
        assert lower < higher
        seen += next_status(ws, order_id, "SHIPPED")  # the watcher forwarded the higher id
        port = shop.stack.order.addr.rsplit(":", 1)[1]
        shop.stack.loop_thread.call(shop.stack.order.service.stop(0.1))  # the stream drops...
        first.execute("SELECT pg_notify('order_events', %s)", (str(lower),))
        first.commit()  # ...and the lower id commits while nobody is listening
        shop.stack.restart_order(address=f"127.0.0.1:{port}")
        seen += next_status(ws, order_id, "PACKED")
    finally:
        first.close()
        second.close()
    assert statuses(seen, order_id) == ["PENDING", "CONFIRMED", "SHIPPED", "PACKED"]  # both arrived, each once
    assert ws.quiet(1.5)  # and nothing is replayed to the client a second time
