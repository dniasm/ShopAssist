"""Cart, checkout and the session WebSocket through the REST API, over real gRPC services and real Postgres."""
import queue
import threading
import time
from dataclasses import dataclass

import pytest
from tests.conftest import AppClient
from starlette.websockets import WebSocketDisconnect

from orchestrator.app import create_app
from orchestrator.settings import Settings
from tests.conftest import start, stop
from tests.helpers import SKU_PRICE, rows, sku_with

STEPS = ["VALIDATING", "RESERVING", "CHARGING", "CONFIRMING"]


class WsReader:
    """Reads a TestClient websocket on a thread so tests can wait with a timeout instead of hanging."""

    def __init__(self, client, path):
        self.items: queue.Queue = queue.Queue()
        self._ctx = client.websocket_connect(path)
        self._ws = self._ctx.__enter__()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        try:
            while True:
                self.items.put(self._ws.receive_json())
        except WebSocketDisconnect as e:
            self.items.put(("closed", e.code))
        except Exception as e:  # the socket was closed under us at teardown
            self.items.put(("closed", repr(e)))

    def get(self, timeout=10):
        return self.items.get(timeout=timeout)

    def until(self, type_, timeout=15):
        """Messages up to and including the first with this type."""
        seen, deadline = [], time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = self.get(max(0.1, deadline - time.monotonic()))
            seen.append(msg)
            if isinstance(msg, dict) and msg["type"] == type_:
                return seen
        raise AssertionError(f"no {type_!r} within {timeout}s; saw {[m if not isinstance(m, dict) else (m['channel'], m['type']) for m in seen]}")

    def quiet(self, seconds=0.6) -> bool:
        try:
            self.items.get(timeout=seconds)
            return False
        except queue.Empty:
            return True

    def close(self):
        self._ctx.__exit__(None, None, None)


@dataclass
class Shop:
    client: object
    stack: object
    readers: list

    def login(self, user_id):
        r = self.client.post("/api/session", json={"user_id": user_id}).json()
        return {"Authorization": f"Bearer {r['token']}"}, r["token"]

    def ws(self, token, after=None, guest=None):
        who = f"guest={guest}" if guest else f"token={token}"
        reader = WsReader(self.client, f"/api/ws?{who}" + (f"&after={after}" if after is not None else ""))
        self.readers.append(reader)
        return reader

    def put(self, headers, sku, qty):
        return self.client.put(f"/api/cart/lines/{sku}", json={"quantity": qty}, headers=headers)

    def checkout(self, headers, key="key-1", method="card_ok"):
        return self.client.post("/api/checkout", json={"idempotency_key": key, "payment_method": method}, headers=headers)

    def payments(self):
        return rows(self.stack.payment.url, "SELECT status FROM payments WHERE payment_id > 'PAY-2'")


@pytest.fixture
def make_shop(make_stack, catalog_running, loop_thread, temp_dbs, seed_dir):
    from services.customer.server import create_service as create_customer

    opened: list[tuple] = []
    customers: list = []

    def make(payment_latency_ms=0, llm=None, **settings_overrides) -> Shop:
        stack = make_stack(payment_latency_ms=payment_latency_ms)
        customer_running = start(loop_thread, temp_dbs, "customer", create_customer, seed_dir=seed_dir)  # fresh carts per test
        customers.append(customer_running)
        settings = Settings(
            catalog_addr=catalog_running.addr, inventory_addr=stack.inventory.addr, customer_addr=customer_running.addr,
            order_addr=stack.order.addr, payment_addr=stack.payment.addr, session_secret="s", **settings_overrides,
        )
        client = AppClient(create_app(settings, llm=llm))
        client.__enter__()
        shop = Shop(client, stack, [])
        opened.append((client, shop))
        return shop

    yield make
    for client, shop in opened:
        for reader in shop.readers:
            try:
                reader.close()
            except Exception:
                pass
        client.__exit__(None, None, None)
    for running in customers:
        stop(loop_thread, temp_dbs, running)


def error(r, status, code):
    assert r.status_code == status, r.text
    assert r.json()["error"]["code"] == code
    return r.json()["error"]


# ---------------- cart ----------------

def test_cart_needs_a_session_and_starts_empty(make_shop):
    shop = make_shop()
    error(shop.client.get("/api/cart"), 401, "unauthenticated")
    headers, _ = shop.login("U-03")
    assert shop.client.get("/api/cart", headers=headers).json() == {
        "lines": [], "subtotal_cents": 0, "shipping_cents": 0, "total_cents": 0, "version": 0}


def test_cart_lines_are_priced_by_the_catalog_and_show_live_availability(make_shop):
    shop = make_shop()
    headers, _ = shop.login("U-04")
    sku = sku_with(2)
    cart = shop.put(headers, sku, 3).json()
    (line,) = cart["lines"]
    assert set(line) == {"sku_id", "product_id", "name", "category", "size", "color", "quantity", "unit_price_cents", "line_total_cents", "available"}
    assert (line["sku_id"], line["quantity"], line["available"]) == (sku, 3, 2)  # may hold more than is in stock; checkout decides
    assert line["unit_price_cents"] == SKU_PRICE[sku] and line["line_total_cents"] == 3 * SKU_PRICE[sku] and line["name"]
    subtotal = 3 * SKU_PRICE[sku]
    assert cart["subtotal_cents"] == subtotal and cart["shipping_cents"] == (0 if subtotal >= 5000 else 499)
    assert cart["total_cents"] == subtotal + cart["shipping_cents"] and cart["version"] == 1


def test_setting_zero_or_deleting_removes_a_line_and_bad_input_is_refused(make_shop):
    shop = make_shop()
    headers, _ = shop.login("U-05")
    a, b = sku_with(4), sku_with(3)
    shop.put(headers, a, 1)
    shop.put(headers, b, 2)
    assert [l["sku_id"] for l in shop.put(headers, a, 0).json()["lines"]] == [b]
    assert shop.client.delete(f"/api/cart/lines/{b}", headers=headers).json()["lines"] == []
    for qty in (11, -1):
        error(shop.put(headers, a, qty), 422, "invalid")
    error(shop.client.put(f"/api/cart/lines/{a}", json={}, headers=headers), 422, "invalid")
    error(shop.put(headers, "SKU-does-not-exist", 1), 404, "not_found")
    assert shop.client.delete("/api/cart/lines/SKU-does-not-exist", headers=headers).status_code == 200  # stale lines can always go


def test_carts_are_private(make_shop):
    shop = make_shop()
    one, _ = shop.login("U-06")
    two, _ = shop.login("U-07")
    shop.put(one, sku_with(4), 1)
    assert shop.client.get("/api/cart", headers=two).json()["lines"] == []


# ---------------- checkout ----------------

def test_checkout_input_is_validated(make_shop):
    shop = make_shop()
    headers, _ = shop.login("U-08")
    error(shop.checkout(headers), 422, "invalid")  # empty cart
    shop.put(headers, sku_with(4), 1)
    error(shop.checkout(headers, method="bitcoin"), 422, "invalid")
    error(shop.client.post("/api/checkout", json={"payment_method": "card_ok"}, headers=headers), 422, "invalid")
    error(shop.client.post("/api/checkout", json={"idempotency_key": "k", "payment_method": "card_ok"}), 401, "unauthenticated")
    assert shop.stack.loop_thread and rows(shop.stack.order.url, "SELECT count(*) FROM orders WHERE order_id > 'O-2'") == [(0,)]


def test_checkout_returns_202_then_streams_steps_and_done_over_the_websocket(make_shop):
    shop = make_shop()
    headers, token = shop.login("U-09")
    ws = shop.ws(token)
    hello = ws.get()
    assert (hello["channel"], hello["type"]) == ("system", "hello") and hello["data"]["user"]["user_id"] == "U-09"
    sku = sku_with(4)
    shop.put(headers, sku, 2)
    r = shop.checkout(headers, "k-ws")
    assert r.status_code == 202 and r.json()["order_id"].startswith("O-")
    msgs = ws.until("done")
    assert {m["channel"] for m in msgs} <= {"checkout", "order"}  # checkout progress, plus the order's own status events
    seqs = [m["seq"] for m in msgs]
    assert seqs == sorted(set(seqs)) and seqs[0] == 1  # strictly increasing, per user
    steps = [(m["data"]["step"], m["data"]["state"]) for m in msgs if m["type"] == "step"]
    assert steps == [(s, st) for s in STEPS for st in ("STARTED", "SUCCEEDED")]
    done = msgs[-1]["data"]["order"]
    assert done["order_id"] == r.json()["order_id"] and done["status"] == "CONFIRMED" and done["user_id"] == "U-09"
    assert done["lines"][0]["quantity"] == 2 and done["total_cents"] == done["subtotal_cents"] + done["shipping_cents"]
    assert shop.client.get("/api/cart", headers=headers).json()["lines"] == []  # cleared before `done` was announced


def test_cart_shipping_preview_equals_the_shipping_the_order_charges(make_shop):
    shop = make_shop()
    headers, token = shop.login("U-10")
    ws = shop.ws(token)
    ws.get()
    for qty in (1, 3):
        cart = shop.put(headers, sku_with(4), qty).json()
        shop.checkout(headers, f"k-ship-{qty}")
        order = ws.until("done")[-1]["data"]["order"]
        assert (order["shipping_cents"], order["total_cents"]) == (cart["shipping_cents"], cart["total_cents"])


def test_double_submit_is_one_order_and_one_charge(make_shop):
    shop = make_shop(payment_latency_ms=300)
    headers, token = shop.login("U-11")
    ws = shop.ws(token)
    ws.get()
    shop.put(headers, sku_with(4), 1)
    first, second = shop.checkout(headers, "dbl"), shop.checkout(headers, "dbl")  # the user clicks twice
    assert first.status_code == second.status_code == 202 and first.json() == second.json()
    msgs = ws.until("done")
    assert ws.quiet(1.0), "a second saga or a second `done` was announced"
    assert [m["type"] for m in msgs].count("step") == 8 and len(shop.payments()) == 1


def test_reusing_a_key_with_a_different_cart_is_409_conflict(make_shop):
    shop = make_shop()
    headers, token = shop.login("U-12")
    ws = shop.ws(token)
    ws.get()
    shop.put(headers, sku_with(4), 1)
    assert shop.checkout(headers, "same").status_code == 202
    ws.until("done")
    shop.put(headers, sku_with(3), 1)
    error(shop.checkout(headers, "same"), 409, "conflict")
    assert len(shop.payments()) == 1


def test_a_declined_card_fails_the_checkout_and_keeps_the_cart(make_shop):
    shop = make_shop()
    headers, token = shop.login("U-03")
    ws = shop.ws(token)
    ws.get()
    shop.put(headers, sku_with(4), 1)
    order_id = shop.checkout(headers, "decl", "card_declined").json()["order_id"]
    failed = ws.until("failed")[-1]["data"]
    assert failed["order_id"] == order_id and "declined" in failed["reason"] and "shortages" not in failed
    assert len(shop.client.get("/api/cart", headers=headers).json()["lines"]) == 1
    assert shop.client.get(f"/api/orders/{order_id}", headers=headers).json()["order"]["status"] == "FAILED"


def test_not_enough_stock_reports_the_shortages_over_the_websocket(make_shop):
    shop = make_shop()
    headers, token = shop.login("U-04")
    ws = shop.ws(token)
    ws.get()
    scarce = sku_with(1)
    shop.put(headers, scarce, 3)
    shop.checkout(headers, "short")
    msgs = ws.until("failed")
    failed = msgs[-1]["data"]
    assert failed["shortages"] == [{"sku_id": scarce, "requested": 3, "available": 1}]
    assert any(m["type"] == "step" and m["data"]["step"] == "RESERVING" and m["data"]["state"] == "FAILED" and m["data"]["shortages"]
               for m in msgs)
    assert len(shop.client.get("/api/cart", headers=headers).json()["lines"]) == 1


def test_a_cart_edited_during_checkout_is_not_cleared(make_shop):
    shop = make_shop(payment_latency_ms=400)
    headers, token = shop.login("U-05")
    ws = shop.ws(token)
    ws.get()
    shop.put(headers, sku_with(4), 1)
    shop.checkout(headers, "race-cart")
    shop.put(headers, sku_with(3), 1)  # the user keeps shopping while the saga runs
    assert ws.until("done")[-1]["data"]["order"]["status"] == "CONFIRMED"
    assert len(shop.client.get("/api/cart", headers=headers).json()["lines"]) == 2  # if_version no longer matched: nothing cleared


def test_retrying_a_finished_checkout_returns_the_order_and_leaves_the_cart_alone(make_shop):
    shop = make_shop()
    headers, token = shop.login("U-06")
    ws = shop.ws(token)
    ws.get()
    sku = sku_with(4)
    shop.put(headers, sku, 1)
    first = shop.checkout(headers, "retry").json()
    ws.until("done")
    shop.put(headers, sku, 1)  # the same cart again
    again = shop.checkout(headers, "retry")
    assert again.status_code == 202 and again.json() == first
    assert ws.until("done")[-1]["data"]["order"]["order_id"] == first["order_id"]
    assert len(shop.client.get("/api/cart", headers=headers).json()["lines"]) == 1  # a replay must not wipe the new cart
    assert len(shop.payments()) == 1


# ---------------- orders ----------------

def test_orders_endpoints_are_scoped_to_the_signed_in_user(make_shop):
    shop = make_shop()
    a, token = shop.login("U-07")
    b, _ = shop.login("U-08")
    ws = shop.ws(token)
    ws.get()
    ids = []
    for i in range(3):
        shop.put(a, sku_with(4), 1)
        ids.append(shop.checkout(a, f"o-{i}").json()["order_id"])
        ws.until("done")
    listing = shop.client.get("/api/orders", headers=a, params={"page_size": 2}).json()
    assert [o["order_id"] for o in listing["orders"]] == [ids[2], ids[1]] and listing["next_page_token"]
    assert all("history" not in o for o in listing["orders"])
    rest = shop.client.get("/api/orders", headers=a, params={"page_size": 2, "page_token": listing["next_page_token"]}).json()
    assert [o["order_id"] for o in rest["orders"]][0] == ids[0] and not rest["next_page_token"]
    detail = shop.client.get(f"/api/orders/{ids[0]}", headers=a).json()["order"]
    assert [h["status"] for h in detail["history"]] == ["PENDING", "CONFIRMED"]
    error(shop.client.get(f"/api/orders/{ids[0]}", headers=b), 404, "not_found")  # another user's order does not exist for you
    error(shop.client.get("/api/orders/O-nope", headers=a), 404, "not_found")
    assert shop.client.get("/api/orders", headers=b).json()["orders"] == []
    error(shop.client.get("/api/orders"), 401, "unauthenticated")


# ---------------- session websocket ----------------

def test_websocket_rejects_a_missing_or_bad_token_with_close_code_4401(make_shop):
    shop = make_shop()
    for path in ("/api/ws", "/api/ws?token=garbage"):
        with shop.client.websocket_connect(path) as ws:
            with pytest.raises(WebSocketDisconnect) as e:
                ws.receive_json()
        assert e.value.code == 4401


def test_reconnecting_with_after_replays_exactly_the_missed_events(make_shop):
    shop = make_shop()
    headers, token = shop.login("U-09")
    live = shop.ws(token)
    live.get()
    shop.put(headers, sku_with(4), 1)
    shop.checkout(headers, "replay")
    seen = live.until("done")
    cut = seen[2]["seq"]  # the tab "dropped" after its third event
    late = shop.ws(token, after=cut)
    hello = late.get()
    assert hello["type"] == "hello" and hello["data"]["seq"] == seen[-1]["seq"]
    replayed = late.until("done")
    assert [m["seq"] for m in replayed] == [m["seq"] for m in seen if m["seq"] > cut]  # no gap, no duplicate
    assert late.quiet()


def test_every_tab_of_a_user_gets_every_event_and_other_users_get_none(make_shop):
    shop = make_shop()
    headers, token = shop.login("U-10")
    _, other_token = shop.login("U-11")
    tab1, tab2, stranger = shop.ws(token), shop.ws(token), shop.ws(other_token)
    for tab in (tab1, tab2, stranger):
        tab.get()
    shop.put(headers, sku_with(4), 1)
    shop.checkout(headers, "tabs")
    a, b = tab1.until("done"), tab2.until("done")
    assert [m["seq"] for m in a] == [m["seq"] for m in b] and len([m for m in a if m["channel"] == "checkout"]) == 9
    assert stranger.quiet(1.0)


def test_a_cursor_older_than_the_buffer_gets_resync_instead_of_a_gappy_replay(make_shop):
    shop = make_shop(ws_buffer_size=3)
    headers, token = shop.login("U-12")
    live = shop.ws(token)
    live.get()
    shop.put(headers, sku_with(4), 1)
    shop.checkout(headers, "resync")
    live.until("done")  # 9 events went by; only the last 3 are kept
    late = shop.ws(token, after=1)
    late.get()  # hello
    msg = late.get()
    assert (msg["channel"], msg["type"]) == ("system", "resync")
    assert late.quiet()
    ahead = shop.ws(token, after=500)  # a cursor from before a server restart
    ahead.get()
    assert ahead.get()["type"] == "resync"


# ---------------- retry after success, interrupted checkouts, WebSocket schema ----------------

def test_retrying_after_success_with_the_now_empty_cart_returns_the_same_order(make_shop):
    shop = make_shop()
    headers, token = shop.login("U-03")
    ws = shop.ws(token)
    ws.get()
    shop.put(headers, sku_with(4), 1)
    first = shop.checkout(headers, "blip")
    ws.until("done")
    assert shop.client.get("/api/cart", headers=headers).json()["lines"] == []
    again = shop.checkout(headers, "blip")  # network blip: the client never saw the 202, so it retries with the same key
    assert again.status_code == 202 and again.json() == first.json()
    assert ws.until("done")[-1]["data"]["order"]["order_id"] == first.json()["order_id"]  # the final event arrives again
    assert shop.client.get("/api/cart", headers=headers).json()["lines"] == []
    assert len(shop.payments()) == 1
    error(shop.checkout(headers, "a-new-key"), 422, "invalid")  # a NEW key with nothing in the cart is still an empty cart
    assert shop.client.post("/api/checkout", json={"idempotency_key": "a-new-key", "payment_method": "card_ok"}, headers=headers).json()[
        "error"]["message"] == "Your cart is empty."


def valid(msg):
    """Every real message must satisfy the published WebSocket schema."""
    from pydantic import TypeAdapter
    from orchestrator.ws_schema import WsMessage
    return TypeAdapter(WsMessage).validate_python(msg)


def test_every_message_the_socket_sends_matches_the_published_schema(make_shop):
    shop = make_shop()
    headers, token = shop.login("U-04")
    ws = shop.ws(token)
    seen = [ws.get()]
    shop.put(headers, sku_with(1), 3)
    shop.checkout(headers, "bad")  # out of stock: steps and a `failed` with shortages
    seen += ws.until("failed")
    shop.put(headers, sku_with(1), 0)
    shop.put(headers, sku_with(4), 1)
    shop.checkout(headers, "good")
    seen += ws.until("done")
    late = shop.ws(token, after=500)
    seen += [late.get(), late.get()]  # hello, resync
    assert {m["type"] for m in seen} == {"hello", "step", "failed", "done", "resync", "status"}
    for m in seen:
        valid(m)


def test_the_websocket_schema_is_published_in_openapi_through_a_schema_only_route(make_shop):
    shop = make_shop()
    spec = shop.client.get("/openapi.json").json()
    route = spec["paths"]["/api/ws/schema"]["get"]
    assert route["responses"]["204"] and route["responses"]["200"]["content"]["application/json"]["schema"]
    schemas = spec["components"]["schemas"]
    for name in ("SystemHello", "SystemResync", "CheckoutStep", "CheckoutDone", "CheckoutFailed", "CheckoutInterrupted", "OrderStatus"):
        assert name in schemas, name
    assert shop.client.get("/api/ws/schema").status_code == 204


def test_losing_the_order_service_mid_checkout_says_interrupted_never_failed(make_shop):
    shop = make_shop(payment_latency_ms=1500)  # the card is slow, so the saga is mid-charge when the order service dies
    headers, token = shop.login("U-05")
    ws = shop.ws(token)
    ws.get()
    shop.put(headers, sku_with(4), 2)
    order_id = shop.checkout(headers, "lost").json()["order_id"]
    started = [ws.get()]
    while started[-1]["data"].get("step") != "CHARGING":
        started.append(ws.get())
    shop.stack.restart_order(recovery_min_age_seconds=0)  # the order service dies and comes back (on a new port)

    after = ws.until("interrupted")
    last = after[-1]
    valid(last)
    assert (last["channel"], last["data"]["order_id"]) == ("checkout", order_id)
    assert "settled automatically" in last["data"]["message"] and "only be charged if it is confirmed" in last["data"]["message"]
    assert not [m for m in after if m["type"] in ("done", "failed")]  # an unknown outcome is never reported as either
    assert shop.client.get("/api/cart", headers=headers).json()["lines"]  # nothing was claimed, so the cart is not cleared

    # recovery then decides, and money and order agree whichever way it went
    from tests.helpers import get_order
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        shop.stack.loop_thread.call(shop.stack.order.service.recover_now())
        order = get_order(shop.stack.order.addr, "U-05", order_id)
        if order.status != 1:  # PENDING
            break
        time.sleep(0.2)
    paid = [p for p in shop.payments() if p[0] == "SUCCEEDED"]
    assert (order.status == 2 and len(paid) == 1) or (order.status == 8 and not paid)  # CONFIRMED+one charge, or FAILED+none


def test_cart_and_order_lines_carry_the_category(make_shop):
    from tests.fixtures import PRODUCTS, SKUS
    shop = make_shop()
    headers, token = shop.login("U-06")
    ws = shop.ws(token)
    ws.get()
    sku = sku_with(4)
    expected = next(p["category"] for p in PRODUCTS if p["product_id"] == next(s["product_id"] for s in SKUS if s["sku_id"] == sku))
    assert shop.put(headers, sku, 1).json()["lines"][0]["category"] == expected
    order_id = shop.checkout(headers, "cat").json()["order_id"]
    ws.until("done")
    assert shop.client.get(f"/api/orders/{order_id}", headers=headers).json()["order"]["lines"][0]["category"] == expected
    assert shop.client.get("/api/orders", headers=headers).json()["orders"][0]["lines"][0]["category"] == expected
