"""The agent's 8 tools against the real services: exact cart lines, no guessing, nothing irreversible, compact results."""
import json
import re

import pytest

from orchestrator.agent.prompt import SYSTEM_PROMPT, TOOLS
from orchestrator.agent.tools import AgentTools
from tests.fixtures import INVENTORY, PRODUCTS, SKUS
from tests.helpers import rows
from tests.test_agent_chat import count_calls
from tests.test_api_shop import make_shop  # noqa: F401  (fixture shared with the shop tests)

USER = "U-03"
BY_SKU = {s["sku_id"]: s for s in SKUS}
STOCK = {i["sku_id"]: i["on_hand"] for i in INVENTORY}
NAME = {p["product_id"]: p["name"] for p in PRODUCTS}


def hoodie_pair():
    """Two in-stock SKUs of one product with the same colour and different sizes."""
    for p in PRODUCTS:
        by_color: dict[str, list[dict]] = {}
        for s in SKUS:
            if s["product_id"] == p["product_id"] and STOCK[s["sku_id"]] >= 2:
                by_color.setdefault(s["color"], []).append(s)
        for same in by_color.values():
            if len(same) >= 2:
                return same[0], same[1]
    raise AssertionError("no product with two stocked sizes in one colour")


class Env:
    def __init__(self, shop, recall=None):
        self.shop = shop
        self.headers, self.token = shop.login(USER)
        self.tools = AgentTools(shop.client.app.state.clients, shop.client.app.state.proposals, recall)

    def run(self, name, args=None, user=USER):
        return self.shop.client.portal.call(self.tools.run, name, args or {}, user, "turn-1")

    def put(self, sku, qty):
        assert self.shop.put(self.headers, sku["sku_id"], qty).status_code == 200

    def lines(self):
        return self.shop.client.get("/api/cart", headers=self.headers).json()["lines"]


@pytest.fixture
def env(make_shop):
    return Env(make_shop())


def test_remove_ambiguous_asks_and_keeps_cart(env):
    m, l = hoodie_pair()
    env.put(m, 1)
    env.put(l, 1)
    for args in ({"action": "remove"}, {"action": "remove", "line_id": "SKU-nope"}, {"action": "remove", "product_id": m["product_id"]}):
        out = env.run("cart", args)
        assert out["error"] is True and out["final"] is None
        assert m["size"] in out["text"] and l["size"] in out["text"] and m["sku_id"] in out["text"] and l["sku_id"] in out["text"]
    assert len(env.lines()) == 2


def test_remove_exact_line_only(env):
    m, l = hoodie_pair()
    env.put(m, 1)
    env.put(l, 1)
    out = env.run("cart", {"action": "remove", "line_id": l["sku_id"]})
    assert out["error"] is False and [x["sku_id"] for x in env.lines()] == [m["sku_id"]]
    assert NAME[l["product_id"]] in out["final"] and l["size"] in out["final"] and "P-" not in out["final"] and "SKU" not in out["final"]


def test_remove_never_guesses_even_with_a_single_matching_line(env):
    m, _ = hoodie_pair()
    env.put(m, 1)
    out = env.run("cart", {"action": "remove", "product_id": m["product_id"], "size": m["size"]})
    assert out["error"] is True and m["sku_id"] in out["text"] and len(env.lines()) == 1


def test_set_qty_exact_line(env):
    m, l = hoodie_pair()
    env.put(m, 1)
    env.put(l, 2)
    out = env.run("cart", {"action": "set_qty", "line_id": m["sku_id"], "quantity": 3})
    assert out["error"] is False, out["text"]
    assert "P-" not in out["final"]
    assert {x["sku_id"]: x["quantity"] for x in env.lines()} == {m["sku_id"]: 3, l["sku_id"]: 2}
    assert env.run("cart", {"action": "set_qty", "line_id": m["sku_id"], "quantity": 99})["final"]  # capped
    assert {x["sku_id"]: x["quantity"] for x in env.lines()}[m["sku_id"]] == 10
    assert env.run("cart", {"action": "set_qty", "quantity": 2})["error"] is True  # no line id: asks


def test_add_missing_variant_asks(env):
    m, _ = hoodie_pair()
    out = env.run("cart", {"action": "add", "product_id": m["product_id"]})
    assert out["final"] and "Sizes:" in out["final"] and "colours:" in out["final"] and out["error"] is False
    assert env.run("cart", {"action": "add", "product_id": m["product_id"], "size": "XXXL", "color": m["color"]})["final"].startswith("Which size")
    assert env.lines() == []  # nothing was written


def test_add_names_product_not_id_and_adds_to_existing_quantity(env):
    m, _ = hoodie_pair()
    out = env.run("cart", {"action": "add", "product_id": m["product_id"], "size": m["size"], "color": m["color"]})
    assert out["final"].startswith(f"Added {NAME[m['product_id']]} ({m['size']}, {m['color']}).") and "Cart total $" in out["final"]
    assert not re.search(r"P-\d|SKU-", out["final"]) and out["card"]["kind"] == "cart"
    env.run("cart", {"action": "add", "product_id": m["product_id"], "size": m["size"], "color": m["color"], "quantity": 20})
    assert env.lines()[0]["quantity"] == 10  # 1 + 20, capped


def test_cart_view_is_compact_text_with_line_ids(env):
    m, _ = hoodie_pair()
    env.put(m, 2)
    out = env.run("cart", {"action": "view"})
    assert out["text"].startswith(f"[{m['sku_id']}] ") and "Total $" in out["text"] and not out["text"].lstrip().startswith("{")
    assert out["card"]["kind"] == "cart" and out["final"] is None


def test_checkout_never_calls_order(env):
    checkouts = count_calls(env.shop, "Checkout")
    m, _ = hoodie_pair()
    env.put(m, 1)
    out = env.run("checkout")
    assert out["proposal"].action == "checkout_redirect" and out["proposal"].summary == "Are you sure you'd like to check out?"
    assert out["proposal"].args["total_cents"] > 0 and "$" in out["final"]
    assert checkouts == [] and rows(env.shop.stack.order.url, "SELECT count(*) FROM orders WHERE order_id > 'O-2'") == [(0,)]
    env.shop.client.put(f"/api/cart/lines/{m['sku_id']}", json={"quantity": 0}, headers=env.headers)
    empty = env.run("checkout")
    assert empty["proposal"] is None and "empty" in empty["final"]


def test_no_tool_can_create_a_legacy_checkout_proposal(env):
    m, _ = hoodie_pair()
    env.put(m, 1)
    for name, args in (("checkout", {}), ("cart", {"action": "view"}), ("orders", {}), ("open_page", {"kind": "orders"})):
        env.run(name, args)
    assert {p.action for p in env.shop.client.app.state.proposals._items.values()} <= {"checkout_redirect", "cancel_order"}


def test_search_pages_and_final_intro(env):
    first = env.run("search", {"category": "hoodies"})
    assert first["final"] == "I found 12 products for you:" and len(first["card"]["data"]["products"]) == 6
    assert first["text"].startswith("12 results (page 1)") and "More on page 2." in first["text"]
    second = env.run("search", {"category": "hoodies", "page": 2})
    ids = lambda o: {p["product_id"] for p in o["card"]["data"]["products"]}  # noqa: E731
    assert len(ids(second)) == 6 and not ids(first) & ids(second)
    similar = env.tools.run
    page = {"product_id": first["card"]["data"]["products"][0]["product_id"], "name": "Alpine Hoodie 1", "category": "hoodies"}
    out = env.shop.client.portal.call(similar, "search", {"category": "hoodies"}, USER, "t", page)
    assert out["final"].startswith("There are 11 similar products to Alpine Hoodie 1")
    assert page["product_id"] not in ids(out)
    none = env.run("search", {"query": "zzzzqqqq"})
    assert none["final"] is None and none["card"]["data"]["products"] == []


def test_product_details_and_stock(env):
    m, _ = hoodie_pair()
    out = env.run("product", {"ids": [m["product_id"]]})
    assert out["final"] is None and out["card"]["kind"] == "product" and NAME[m["product_id"]] in out["text"] and "Stock:" in out["text"]
    narrowed = env.run("product", {"ids": [m["product_id"]], "size": m["size"], "color": m["color"]})
    assert narrowed["card"]["kind"] == "stock" and narrowed["card"]["data"]["items"][0]["sku_id"] == m["sku_id"]
    several = env.run("product", {"ids": [m["product_id"], "P-9999"]})
    assert several["error"] is False and "not found" in several["text"]
    assert env.run("product", {"ids": ["P-9999"]})["error"] is True


def test_open_page_paths(env):
    m, _ = hoodie_pair()
    assert env.run("open_page", {"kind": "product", "product_id": m["product_id"]})["navigate"] == {"path": f"/p/{m['product_id']}"}
    assert env.run("open_page", {"kind": "shop", "category": "hoodies", "query": "navy"})["navigate"] == {"path": "/shop?category=hoodies&q=navy"}
    assert env.run("open_page", {"kind": "shop"})["navigate"] == {"path": "/shop"}
    assert env.run("open_page", {"kind": "orders"})["navigate"] == {"path": "/orders"}
    assert env.run("open_page", {"kind": "order", "order_id": "O-9999"})["error"] is True
    assert env.run("open_page", {"kind": "checkout"})["error"] is True  # there is no way to open /checkout
    assert env.run("open_page", {"kind": "product", "product_id": "P-9999"})["error"] is True  # never an invented id
    assert env.run("open_page", {"kind": "orders"}, user=None)["card"]["kind"] == "signin"


def test_orders_and_cancel_proposal(env):
    ws = env.shop.ws(env.token)
    ws.get()
    empty = env.run("orders")
    assert empty["text"] == "No orders yet." and empty["final"] == "You have no orders yet."
    m, _ = hoodie_pair()
    env.put(m, 1)
    order_id = env.shop.checkout(env.headers, "k-tools").json()["order_id"]
    ws.until("done")
    out = env.run("orders")
    assert out["card"]["kind"] == "orders" and out["text"].startswith(order_id) and "confirmed" in out["text"]
    assert out["final"] == "Here are your latest orders:"
    detail = env.run("orders", {"order_id": order_id})
    assert detail["card"]["kind"] == "order" and detail["card"]["data"]["order"]["order_id"] == order_id
    assert detail["final"].startswith(f"Order {order_id} is confirmed: 1 × ") and "P-" not in detail["final"]
    assert env.run("open_page", {"kind": "order", "order_id": order_id})["navigate"] == {"path": f"/orders/{order_id}"}
    cancels = count_calls(env.shop, "CancelOrder")
    proposed = env.run("cancel_order", {"order_id": order_id})
    assert cancels == [] and proposed["proposal"].action == "cancel_order" and proposed["proposal"].args == {"order_id": order_id}
    assert "Nothing is cancelled until you confirm" in proposed["final"]
    assert env.run("cancel_order", {"order_id": "O-9999"})["error"] is True


def test_account_tools_need_a_user(env):
    for name, args in (("cart", {"action": "view"}), ("orders", {}), ("cancel_order", {"order_id": "O-1"}), ("checkout", {})):
        out = env.run(name, args, user=None)
        assert out["card"]["kind"] == "signin" and out["needs_sign_in"] is True and out["proposal"] is None


def test_recall_facts_uses_the_injected_reader(make_shop):
    calls = []

    async def recall(user_id, query):
        calls.append((user_id, query))
        return ["likes navy", "wears size M"]

    env = Env(make_shop(), recall)
    assert env.run("recall_facts", {"query": "colour"})["text"] == "- likes navy\n- wears size M" and calls == [(USER, "colour")]
    assert "unavailable" in Env(make_shop()).run("recall_facts", {"query": "x"})["text"]


def test_unknown_tool_and_bad_args_are_errors_not_crashes(env):
    assert env.run("propose_checkout")["error"] is True
    assert env.run("cart", {"action": "fly"})["error"] is True
    assert env.run("cancel_order", {})["error"] is True


def test_tools_schema_under_700_tokens():
    assert len(json.dumps(TOOLS)) // 4 < 700
    assert [t["function"]["name"] for t in TOOLS] == ["search", "product", "cart", "orders", "cancel_order", "checkout", "open_page", "recall_facts"]
    for t in TOOLS:
        assert t["type"] == "function" and t["function"]["parameters"]["type"] == "object"


def test_system_prompt_under_600_tokens():
    assert len(SYSTEM_PROMPT) // 4 < 600
    for must in ("never pay", "ambiguous", "latest message", "checkout"):
        assert must in SYSTEM_PROMPT.lower()
