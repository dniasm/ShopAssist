"""The assistant end to end: POST /api/chat, events on the session WebSocket, proposals and confirmation. Real services and
databases; the only fake is the LLM (a scripted object, or none for the rules planner)."""
import pytest

from orchestrator.llm import LLMError
from tests.fixtures import PRODUCTS, SKUS
from tests.helpers import advance, rows, sku_with
from tests.test_agent_planner import NO_ARGS, ScriptedLLM, model_says
from tests.test_api_shop import error, make_shop, valid  # noqa: F401  (fixtures shared with the shop tests)

GUEST = "guest-visitor-0001"


def say(shop, headers, message, context=None, **extra):
    r = shop.client.post("/api/chat", json={"message": message, "context": context or {"path": "/shop"}, **extra}, headers=headers or {})
    assert r.status_code == 202, r.text
    return r.json()["turn_id"]


def turn(ws):
    """Every message of one turn, from turn_started to turn_done (validated against the published schema)."""
    msgs = ws.until("turn_done")
    for m in msgs:
        valid(m)
    chat = [m for m in msgs if m["channel"] == "chat"]
    assert chat[0]["type"] == "turn_started" and chat[-1]["type"] == "turn_done"
    return chat


def types(msgs):
    return [m["type"] for m in msgs]


def sku_row(on_hand=4):
    sku = next(s for s in SKUS if s["sku_id"] == sku_with(on_hand))
    return sku["product_id"], sku["size"], sku["color"], sku["sku_id"]


def open_chat(shop, user="U-03"):
    headers, token = shop.login(user)
    ws = shop.ws(token)
    ws.get()
    return headers, ws


def test_one_turn_streams_in_order_and_ends_with_cards_and_a_source(make_shop):
    shop = make_shop()
    headers, ws = open_chat(shop)
    turn_id = say(shop, headers, "show me hoodies")
    msgs = turn(ws)
    assert types(msgs) == ["turn_started", "tool_call", "tool_result", "message", "turn_done"]
    assert {m["data"]["turn_id"] for m in msgs} == {turn_id}
    assert msgs[1]["data"] == {"turn_id": turn_id, "tool": "search_products", "args": {"category": "hoodies"}}
    message = msgs[3]["data"]
    assert message["cards"][0]["kind"] == "products" and message["cards"][0]["data"]["total"] == 12
    # the cards name and link the products, so the words only introduce them and ask a question after them
    assert message["text"] == "I found 12 products for you:" and message["closing"] == "Do any of them pique your interest?"
    assert msgs[4]["data"]["source"] == "rules"
    assert [m["seq"] for m in msgs] == sorted(m["seq"] for m in msgs)


def test_a_follow_up_keeps_the_earlier_category(make_shop):
    shop = make_shop()
    headers, ws = open_chat(shop)
    say(shop, headers, "show me hoodies")
    turn(ws)
    say(shop, headers, "what about in navy?")
    msgs = turn(ws)
    call = next(m for m in msgs if m["type"] == "tool_call")
    assert call["data"]["args"] == {"category": "hoodies", "color": "navy"}
    cards = next(m for m in msgs if m["type"] == "message")["data"]["cards"]
    assert all(p["category"] == "hoodies" and "navy" in p["colors"] for p in cards[0]["data"]["products"])


def test_a_stock_question_on_a_product_page_uses_the_page_product(make_shop):
    shop = make_shop()
    headers, ws = open_chat(shop)
    pid, size, color, _ = sku_row()
    say(shop, headers, f"do you have it in {color}?", {"path": f"/p/{pid}", "product_id": pid})
    msgs = turn(ws)
    assert next(m for m in msgs if m["type"] == "tool_call")["data"]["args"] == {"product_id": pid, "color": color}
    card = next(m for m in msgs if m["type"] == "message")["data"]["cards"][0]
    assert card["kind"] == "stock" and card["data"]["product_id"] == pid and all(i["color"] == color for i in card["data"]["items"])


def test_add_to_cart_runs_directly_and_the_cart_changes(make_shop):
    shop = make_shop()
    headers, ws = open_chat(shop)
    pid, size, color, sku = sku_row()
    say(shop, headers, f"add {pid} size {size} {color} to my cart")
    msgs = turn(ws)
    assert next(m for m in msgs if m["type"] == "tool_call")["data"]["tool"] == "add_to_cart"
    assert next(m for m in msgs if m["type"] == "message")["data"]["cards"][0]["kind"] == "cart"
    assert [l["sku_id"] for l in shop.client.get("/api/cart", headers=headers).json()["lines"]] == [sku]


def test_signed_out_visitors_can_ask_about_products_but_get_a_sign_in_card_for_the_cart(make_shop):
    shop = make_shop()
    ws = shop.ws(None, guest=GUEST)
    hello = ws.get()
    assert hello["type"] == "hello" and hello["data"]["user"] is None
    say(shop, None, "show me jackets", guest_id=GUEST)
    assert next(m for m in turn(ws) if m["type"] == "message")["data"]["cards"][0]["kind"] == "products"
    say(shop, None, "what's in my cart", guest_id=GUEST)
    msgs = turn(ws)
    assert "tool_call" not in types(msgs)  # nothing was called for someone we cannot identify
    message = next(m for m in msgs if m["type"] == "message")["data"]
    assert message["cards"][0]["kind"] == "signin" and "sign in" in message["text"].lower()
    error(shop.client.post("/api/chat", json={"message": "hi"}), 422, "invalid")  # neither a session nor a guest id
    error(shop.client.post("/api/chat/proposals/PR-x/confirm", json={}), 401, "unauthenticated")


def count_calls(shop, name):
    """Count calls to an Order service method made through the orchestrator's real gRPC stub."""
    stub = shop.client.app.state.clients.order
    original, calls = getattr(stub, name), []

    def counting(request, **kw):
        calls.append(request)
        return original(request, **kw)

    setattr(stub, name, counting)
    return calls


# Invariant: checkout never orders. The assistant only proposes going to the checkout page; the Order service sees no call.
# (Confirming a checkout_redirect proposal is reworked in the routes task; the old "confirm places the order" test is gone.)
def test_checkout_is_only_a_redirect_proposal_and_never_orders(make_shop):
    shop = make_shop()
    headers, ws = open_chat(shop)
    checkouts = count_calls(shop, "Checkout")
    pid, size, color, sku = sku_row()
    shop.put(headers, sku, 1)
    say(shop, headers, "ok I'm ready to pay, place the order")
    msgs = turn(ws)
    proposal = next(m for m in msgs if m["type"] == "proposal")["data"]
    assert proposal["action"] == "checkout_redirect" and proposal["proposal_id"].startswith("PR-")
    assert types(msgs).index("proposal") < types(msgs).index("message")
    assert "Are you sure you'd like to check out?" in next(m for m in msgs if m["type"] == "message")["data"]["text"]
    assert checkouts == [] and rows(shop.stack.order.url, "SELECT count(*) FROM orders WHERE order_id > 'O-2'") == [(0,)]
    assert len(shop.client.get("/api/cart", headers=headers).json()["lines"]) == 1  # still in the cart


def test_another_users_proposal_is_not_found_and_a_dismissed_one_cannot_be_confirmed(make_shop):
    shop = make_shop()
    headers, ws = open_chat(shop, "U-04")
    other, _ = shop.login("U-05")
    shop.put(headers, sku_row()[3], 1)
    say(shop, headers, "checkout")
    proposal_id = next(m for m in turn(ws) if m["type"] == "proposal")["data"]["proposal_id"]
    error(shop.client.post(f"/api/chat/proposals/{proposal_id}/confirm", headers=other), 404, "not_found")
    error(shop.client.post("/api/chat/proposals/PR-unknown/confirm", headers=headers), 404, "not_found")
    assert shop.client.post(f"/api/chat/proposals/{proposal_id}/dismiss", headers=headers).json() == {"ok": True}
    error(shop.client.post(f"/api/chat/proposals/{proposal_id}/confirm", headers=headers), 409, "conflict")


def test_an_expired_proposal_is_not_found(make_shop):
    shop = make_shop()
    headers, ws = open_chat(shop, "U-06")
    shop.put(headers, sku_row()[3], 1)
    say(shop, headers, "checkout")
    proposal_id = next(m for m in turn(ws) if m["type"] == "proposal")["data"]["proposal_id"]
    shop.client.app.state.proposals._items[proposal_id].expires_at = 0  # ten minutes later
    error(shop.client.post(f"/api/chat/proposals/{proposal_id}/confirm", headers=headers), 404, "not_found")


def place_order(shop, headers, ws, key):
    shop.put(headers, sku_row()[3], 1)
    order_id = shop.checkout(headers, key).json()["order_id"]
    ws.until("done")
    return order_id


def test_cancelling_a_packed_order_is_proposed_then_done_on_confirm(make_shop):
    shop = make_shop()
    headers, ws = open_chat(shop, "U-07")
    order_id = place_order(shop, headers, ws, "c1")
    advance(shop.stack)  # PACKED
    cancels = count_calls(shop, "CancelOrder")
    say(shop, headers, f"cancel {order_id}")
    proposal = next(m for m in turn(ws) if m["type"] == "proposal")["data"]
    assert (proposal["action"], proposal["args"]) == ("cancel_order", {"order_id": order_id}) and cancels == []
    assert shop.client.post(f"/api/chat/proposals/{proposal['proposal_id']}/confirm", headers=headers).status_code == 202
    assert shop.client.get(f"/api/orders/{order_id}", headers=headers).json()["order"]["status"] == "CANCELLED"
    assert len(cancels) == 1 and cancels[0].idempotency_key == proposal["proposal_id"]


def test_cancelling_a_shipped_order_is_explained_not_proposed(make_shop):
    shop = make_shop()
    headers, ws = open_chat(shop, "U-08")
    order_id = place_order(shop, headers, ws, "c2")
    advance(shop.stack)
    advance(shop.stack)  # SHIPPED
    say(shop, headers, f"cancel {order_id}")
    msgs = turn(ws)
    assert "proposal" not in types(msgs)
    text = next(m for m in msgs if m["type"] == "message")["data"]["text"]
    assert "shipped" in text.lower() and "can't be cancelled" in text
    assert not shop.client.app.state.proposals._items


def scripted_shop(make_shop, *outputs):
    llm = ScriptedLLM(*outputs)
    return make_shop(llm=llm), llm


def test_with_a_model_a_tool_runs_then_the_model_writes_the_answer(make_shop):
    shop, llm = scripted_shop(make_shop, model_says("search_products", category="jeans", max_price_cents=10000),
                              model_says("reply", "Here are some jeans under $100 (P-1017)."))
    headers, ws = open_chat(shop)
    say(shop, headers, "jeans under a hundred")
    msgs = turn(ws)
    assert types(msgs) == ["turn_started", "tool_call", "tool_result", "message", "turn_done"]
    assert msgs[1]["data"]["args"] == {"category": "jeans", "max_price_cents": 10000}
    assert next(m for m in msgs if m["type"] == "message")["data"]["text"] == "Here are some jeans under $100 (P-1017)."  # no matches: the model's words stay
    assert msgs[-1]["data"]["source"] == "llm" and len(llm.calls) == 2
    assert "Tool results so far this turn" in llm.calls[1]["user"]  # the second call saw the first tool's result


def test_a_turn_never_runs_more_than_four_steps(make_shop):
    categories = ["hoodies", "jeans", "shoes", "watches", "jackets", "backpacks"]
    shop, llm = scripted_shop(make_shop, *[model_says("search_products", category=c) for c in categories])
    headers, ws = open_chat(shop)
    say(shop, headers, "show me everything")
    msgs = turn(ws)
    assert types(msgs).count("tool_call") == 4 and len(llm.calls) == 4
    assert types(msgs)[-2:] == ["message", "turn_done"]  # still answered, from what the tools said


def test_a_repeated_call_is_not_run_again_and_its_answer_is_said_once(make_shop):
    shop, llm = scripted_shop(make_shop, *[model_says("get_cart")] * 6)
    headers, ws = open_chat(shop)
    say(shop, headers, "what is in my cart")
    msgs = turn(ws)
    assert types(msgs).count("tool_call") == 1 and len(llm.calls) == 2  # the second plan repeated the call: answer instead
    summary = next(m for m in msgs if m["type"] == "tool_result")["data"]["summary"]
    assert next(m for m in msgs if m["type"] == "message")["data"]["text"] == summary


def test_on_a_product_page_a_search_is_shown_as_similar_products_without_the_product_itself(make_shop):
    product = next(p for p in PRODUCTS if p["category"] == "hoodies")
    shop, llm = scripted_shop(make_shop, model_says("search_products", category="hoodies"),
                              model_says("reply", "1. Something (P-1086) $28.99; 2. Something else (P-1169) $47.99."),
                              model_says("search_products", category="shoes"), model_says("reply", "Some shoes."))
    headers, ws = open_chat(shop)
    say(shop, headers, "anything similar?", {"path": f"/p/{product['product_id']}", "product_id": product["product_id"]})
    message = next(m for m in turn(ws) if m["type"] == "message")["data"]
    card = message["cards"][-1]["data"]
    assert product["product_id"] not in [p["product_id"] for p in card["products"]]
    assert message["text"] == f"There are {card['total']} similar products to {product['name']}:"
    assert message["closing"] == "Do any of them pique your interest?"

    say(shop, headers, "show me shoes", {"path": f"/p/{product['product_id']}", "product_id": product["product_id"]})
    message = next(m for m in turn(ws) if m["type"] == "message")["data"]
    assert message["text"].startswith("I found ")  # another category: not "similar to" the hoodie on the page


def test_a_model_failure_mid_turn_falls_back_to_the_rules_and_says_so(make_shop):
    shop, _ = scripted_shop(make_shop, LLMError("no API key has rate-limit headroom"))
    headers, ws = open_chat(shop)
    say(shop, headers, "show me watches")
    msgs = turn(ws)
    assert msgs[-1]["data"]["source"] == "rules" and next(m for m in msgs if m["type"] == "tool_call")["data"]["args"] == {"category": "watches"}


def test_the_model_cannot_check_out_by_itself(make_shop):
    """Even if the model asks for propose_checkout, the most that happens is a proposal event: the order needs the click."""
    shop, _ = scripted_shop(make_shop, model_says("propose_checkout"), model_says("reply", "Ready when you are."))
    headers, ws = open_chat(shop)
    checkouts = count_calls(shop, "Checkout")
    shop.put(headers, sku_row()[3], 1)
    say(shop, headers, "buy it")
    msgs = turn(ws)
    assert "proposal" in types(msgs) and checkouts == []


def test_a_new_conversation_id_starts_with_fresh_memory_and_the_page_beats_old_chat(make_shop):
    shop = make_shop()
    headers, ws = open_chat(shop, "U-09")
    pid = PRODUCTS[0]["product_id"]  # a hoodie product page
    say(shop, headers, "show me headphones", conversation_id="chat-A")
    turn(ws)
    say(shop, headers, "what about in navy?", conversation_id="chat-A")  # same conversation: the headphones search carries on
    assert next(m for m in turn(ws) if m["type"] == "tool_call")["data"]["args"] == {"category": "headphones", "color": "navy"}
    say(shop, headers, "Is it in stock?", {"path": f"/p/{pid}", "product_id": pid}, conversation_id="chat-A")
    call = next(m for m in turn(ws) if m["type"] == "tool_call")
    assert call["data"]["tool"] == "get_stock" and call["data"]["args"] == {"product_id": pid}  # the page, not the headphones
    say(shop, headers, "what about in navy?", conversation_id="chat-B")  # a new conversation knows nothing of chat-A
    msgs = turn(ws)
    assert "tool_call" not in types(msgs) and "Which product" in next(m for m in msgs if m["type"] == "message")["data"]["text"] \
        or "not sure" in next(m for m in msgs if m["type"] == "message")["data"]["text"]
