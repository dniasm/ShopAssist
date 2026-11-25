"""The assistant end to end: POST /api/chat, events on the session WebSocket, proposals and confirmation. Real services and
databases; the only fake is the LLM (a scripted object that records every request, or none for the rules fallback)."""
import copy
import json

import pytest

from orchestrator.llm import LLMError
from shopassist_proto import shopassist_pb2 as pb
from tests.fixtures import PRODUCTS, SKUS
from tests.helpers import advance, rows, sku_with
from tests.test_api_shop import error, valid
from tests.test_api_shop import make_shop as shop_factory  # noqa: F401  (fixture shared with the shop tests)

GUEST = "guest-visitor-0001"


@pytest.fixture
def make_shop(shop_factory, assistant_running):
    """The shop tests' stack plus a real assistant service, so conversations are really saved."""
    return lambda **kw: shop_factory(assistant_addr=assistant_running.addr, **kw)


class FakeLLM:
    """Stands in for LLMClient: returns the queued responses (or raises queued exceptions) and records each request."""

    def __init__(self, *responses):
        self.queue, self.requests = list(responses), []

    async def chat(self, messages, tools, **kw):
        self.requests.append(copy.deepcopy(messages))
        out = self.queue.pop(0)
        if isinstance(out, Exception):
            raise out
        return out

    async def complete_json(self, *a, **kw):  # the memory worker's calls: nothing to remember here
        raise LLMError("fake")


def says(text, usage=10):
    return {"content": text, "tool_calls": [], "usage": usage}


def uses(*calls, usage=10):
    return {"content": None, "usage": usage,
            "tool_calls": [{"id": f"call-{i}", "name": name, "args": args} for i, (name, args) in enumerate(calls)]}


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


def message_of(msgs):
    return next(m for m in msgs if m["type"] == "message")["data"]


NAME_OF = {p["product_id"]: p["name"] for p in PRODUCTS}


def sku_row(on_hand=4):
    sku = next(s for s in SKUS if s["sku_id"] == sku_with(on_hand))
    return sku["product_id"], sku["size"], sku["color"], sku["sku_id"]


def open_chat(shop, user="U-03"):
    headers, token = shop.login(user)
    ws = shop.ws(token)
    ws.get()
    return headers, ws


def llm_shop(make_shop, *responses):
    llm = FakeLLM(*responses)
    return make_shop(llm=llm), llm


def saved(shop, user, conversation_id):
    clients = shop.client.app.state.clients

    async def read():
        reply = await clients.call("assistant", clients.assistant.ListMessages,
                                   pb.ListMessagesRequest(conversation_id=conversation_id, limit=20), user_id=user)
        return [(m.role, m.text, m.cards_json) for m in reply.messages]

    return shop.client.portal.call(read)


def count_calls(shop, name):
    """Count calls to an Order service method made through the orchestrator's real gRPC stub."""
    stub = shop.client.app.state.clients.order
    original, calls = getattr(stub, name), []

    def counting(request, **kw):
        calls.append(request)
        return original(request, **kw)

    setattr(stub, name, counting)
    return calls


# ---------------- the model loop ----------------

def test_simple_add_is_one_llm_call(make_shop):
    pid, size, color, sku = sku_row()
    shop, llm = llm_shop(make_shop, uses(("cart", {"action": "add", "product_id": pid, "size": size, "color": color})))
    headers, ws = open_chat(shop)
    say(shop, headers, f"add that in {size} {color}")
    msgs = turn(ws)
    assert types(msgs) == ["turn_started", "conversation", "tool_call", "tool_result", "message", "turn_done"]
    assert len(llm.requests) == 1 and msgs[-1]["data"]["source"] == "llm"
    assert message_of(msgs)["text"].startswith("Added ") and message_of(msgs)["cards"][0]["kind"] == "cart"
    assert [l["sku_id"] for l in shop.client.get("/api/cart", headers=headers).json()["lines"]] == [sku]


def test_a_search_for_a_requested_add_does_not_end_the_turn(make_shop):
    pid, size, color, sku = sku_row()
    shop, llm = llm_shop(make_shop, uses(("search", {"query": NAME_OF[pid]})),
                         uses(("cart", {"action": "add", "product_id": pid, "size": size, "color": color})))
    headers, ws = open_chat(shop)
    say(shop, headers, f"find {NAME_OF[pid]} and add it in {size} {color}")
    msgs = turn(ws)
    assert len(llm.requests) == 2 and types(msgs).count("tool_call") == 2
    assert message_of(msgs)["text"].startswith("Added ")
    assert [l["sku_id"] for l in shop.client.get("/api/cart", headers=headers).json()["lines"]] == [sku]


def test_compare_needs_second_call(make_shop):
    a, b = PRODUCTS[0]["product_id"], PRODUCTS[1]["product_id"]
    shop, llm = llm_shop(make_shop, uses(("product", {"ids": [a]}), ("product", {"ids": [b]})), says("The first one is cheaper."))
    headers, ws = open_chat(shop)
    say(shop, headers, "compare those two")
    msgs = turn(ws)
    assert types(msgs).count("tool_call") == 2 and len(llm.requests) == 2
    assert message_of(msgs)["text"] == "The first one is cheaper."
    tool_messages = [m for m in llm.requests[1] if m["role"] == "tool"]  # the second call saw both results, tied to the call ids
    assert [m["tool_call_id"] for m in tool_messages] == ["call-0", "call-1"]
    assistant = next(m for m in llm.requests[1] if m.get("tool_calls"))
    assert [c["function"]["name"] for c in assistant["tool_calls"]] == ["product", "product"]
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {"ids": [a]}


def test_search_is_answered_by_cards_with_the_closing_question(make_shop):
    shop, llm = llm_shop(make_shop, uses(("search", {"category": "hoodies"})))
    headers, ws = open_chat(shop)
    say(shop, headers, "show me hoodies")
    message = message_of(turn(ws))
    assert message["cards"][0]["kind"] == "products" and message["cards"][0]["data"]["total"] == 12
    assert message["text"] == "I found 12 products for you:" and message["closing"] == "Do any of them pique your interest?"
    assert len(llm.requests) == 1


def test_the_model_sees_system_prompt_memory_block_and_cart(make_shop):
    pid, size, color, sku = sku_row()
    shop, llm = llm_shop(make_shop, says("Hi!"))
    headers, ws = open_chat(shop)
    shop.put(headers, sku, 1)
    say(shop, headers, "hello", {"path": f"/p/{pid}", "product_id": pid})
    turn(ws)
    first, memory, last = llm.requests[0][0], llm.requests[0][1], llm.requests[0][-1]
    assert first["role"] == "system" and memory["role"] == "system" and last == {"role": "user", "content": "hello"}
    assert memory["content"].startswith("Memory") and f"[{sku}]" in memory["content"] and f"Page: /p/{pid}" in memory["content"]
    assert next(p["name"] for p in PRODUCTS if p["product_id"] == pid) in memory["content"]


def test_a_repeated_call_ends_the_turn_with_its_result(make_shop):
    shop, llm = llm_shop(make_shop, uses(("cart", {"action": "view"})), uses(("cart", {"action": "view"})), says("never used"))
    headers, ws = open_chat(shop)
    say(shop, headers, "what is in my cart")
    msgs = turn(ws)
    assert types(msgs).count("tool_call") == 1 and len(llm.requests) == 2
    assert message_of(msgs)["text"] == "Cart is empty."


def test_a_turn_never_runs_more_than_six_model_calls(make_shop):
    steps = [uses(("product", {"ids": [p["product_id"]]})) for p in PRODUCTS[:8]]
    shop, llm = llm_shop(make_shop, *steps)
    headers, ws = open_chat(shop)
    say(shop, headers, "tell me about everything")
    msgs = turn(ws)
    assert len(llm.requests) == 6 and types(msgs).count("tool_call") == 6 and types(msgs)[-2:] == ["message", "turn_done"]


def test_on_a_product_page_a_search_is_shown_as_similar_without_the_product_itself(make_shop):
    product = next(p for p in PRODUCTS if p["category"] == "hoodies")
    page = {"path": f"/p/{product['product_id']}", "product_id": product["product_id"]}
    shop, _ = llm_shop(make_shop, uses(("search", {"category": "hoodies"})), uses(("search", {"category": "shoes"})))
    headers, ws = open_chat(shop)
    say(shop, headers, "anything similar?", page)
    message = message_of(turn(ws))
    card = message["cards"][-1]["data"]
    assert product["product_id"] not in [p["product_id"] for p in card["products"]]
    assert message["text"] == f"There are {card['total']} similar products to {product['name']}:"
    say(shop, headers, "show me shoes", page)
    assert message_of(turn(ws))["text"].startswith("I found ")  # another category: not "similar to" the hoodie on the page


def test_navigate_event_emitted(make_shop):
    pid = PRODUCTS[0]["product_id"]
    shop, _ = llm_shop(make_shop, uses(("open_page", {"kind": "product", "product_id": pid})))
    headers, ws = open_chat(shop)
    say(shop, headers, "open that one")
    msgs = turn(ws)
    assert next(m for m in msgs if m["type"] == "navigate")["data"]["path"] == f"/p/{pid}"
    assert types(msgs).index("navigate") < types(msgs).index("message")


def test_reply_has_no_product_ids(make_shop):
    a, b = PRODUCTS[0], PRODUCTS[1]
    text = f"Take the {a['name']} ({a['product_id']}) or {b['product_id']}, not P-9999 or [P-8888]."
    shop, _ = llm_shop(make_shop, uses(("product", {"ids": [a["product_id"]]}), ("product", {"ids": [b["product_id"]]})), says(text))
    headers, ws = open_chat(shop)
    say(shop, headers, "which one?")
    out = message_of(turn(ws))["text"]
    assert "P-" not in out and a["name"] in out and b["name"] in out


def test_without_ids_unit():
    from orchestrator.agent.loop import AgentLoop

    results = [{"card": {"kind": "products", "data": {"products": [{"product_id": "P-1", "name": "Navy Hoodie"}]}}}]
    assert AgentLoop.without_ids("Try (P-1) now", results) == "Try Navy Hoodie now"
    assert AgentLoop.without_ids("Navy Hoodie [P-1] is good", results) == "Navy Hoodie is good"
    assert AgentLoop.without_ids("P-1 and P-22", results) == "Navy Hoodie and"
    assert AgentLoop.without_ids("nothing here", []) == "nothing here"


# ---------------- proposals: the model never orders or cancels by itself ----------------

# Invariant: checkout never orders. The assistant only proposes going to the checkout page; the Order service sees no call.
def test_checkout_is_only_a_redirect_proposal_and_never_orders(make_shop):
    shop, _ = llm_shop(make_shop, uses(("checkout", {})))
    headers, ws = open_chat(shop)
    checkouts = count_calls(shop, "Checkout")
    shop.put(headers, sku_row()[3], 1)
    say(shop, headers, "ok I'm ready to pay, place the order")
    msgs = turn(ws)
    proposal = next(m for m in msgs if m["type"] == "proposal")["data"]
    assert proposal["action"] == "checkout_redirect" and proposal["proposal_id"].startswith("PR-")
    assert types(msgs).index("proposal") < types(msgs).index("message")
    assert "Are you sure you'd like to check out?" in message_of(msgs)["text"]
    assert checkouts == [] and rows(shop.stack.order.url, "SELECT count(*) FROM orders WHERE order_id > 'O-2'") == [(0,)]
    assert len(shop.client.get("/api/cart", headers=headers).json()["lines"]) == 1  # still in the cart


def test_another_users_proposal_is_not_found_and_a_dismissed_one_cannot_be_confirmed(make_shop):
    shop, _ = llm_shop(make_shop, uses(("checkout", {})))
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
    shop, _ = llm_shop(make_shop, uses(("checkout", {})))
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
    shop.client.app.state.agent.llm = FakeLLM(uses(("cancel_order", {"order_id": order_id})))
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
    shop.client.app.state.agent.llm = FakeLLM(uses(("cancel_order", {"order_id": order_id})))
    say(shop, headers, f"cancel {order_id}")
    msgs = turn(ws)
    assert "proposal" not in types(msgs)
    text = message_of(msgs)["text"]
    assert "shipped" in text.lower() and "can't be cancelled" in text
    assert not shop.client.app.state.proposals._items


# ---------------- no model ----------------

def test_llm_down_no_write(make_shop):
    pid, size, color, sku = sku_row()
    shop, _ = llm_shop(make_shop, LLMError("no API key has rate-limit headroom"))
    headers, ws = open_chat(shop)
    shop.put(headers, sku, 1)
    before = shop.client.get("/api/cart", headers=headers).json()["lines"]
    say(shop, headers, "remove the hoodie")
    msgs = turn(ws)
    assert "try again" in message_of(msgs)["text"] and "tool_call" not in types(msgs) and msgs[-1]["data"]["source"] == "rules"
    assert shop.client.get("/api/cart", headers=headers).json()["lines"] == before


def test_llm_down_search_still_works(make_shop):
    shop, _ = llm_shop(make_shop, LLMError("down"))
    headers, ws = open_chat(shop)
    say(shop, headers, "show me hoodies")
    msgs = turn(ws)
    assert next(m for m in msgs if m["type"] == "tool_call")["data"]["args"] == {"category": "hoodies"}
    message = message_of(msgs)
    assert message["cards"][0]["kind"] == "products" and message["text"].startswith("I found ")
    assert message["closing"] == "Do any of them pique your interest?" and msgs[-1]["data"]["source"] == "rules"


def test_without_a_model_the_rules_still_answer_and_never_write(make_shop):
    shop = make_shop()
    headers, ws = open_chat(shop)
    say(shop, headers, "show me hoodies")
    assert message_of(turn(ws))["cards"][0]["data"]["total"] == 12
    pid, size, color, sku = sku_row()
    say(shop, headers, f"add {pid} size {size} {color} to my cart")
    assert "try again" in message_of(turn(ws))["text"]
    assert shop.client.get("/api/cart", headers=headers).json()["lines"] == []
    say(shop, headers, "checkout")
    assert "proposal" not in types(turn(ws))


# ---------------- saving and memory ----------------

def test_messages_saved_for_signed_in(make_shop):
    shop, llm = llm_shop(make_shop, uses(("search", {"category": "hoodies"})), says("Anything else?"))
    headers, ws = open_chat(shop, "U-03")
    say(shop, headers, "show me hoodies please")
    msgs = turn(ws)
    created = next(m for m in msgs if m["type"] == "conversation")["data"]["conversation_id"]
    assert created.startswith("C-") and types(msgs).index("conversation") < types(msgs).index("message")
    rows_ = saved(shop, "U-03", created)
    assert [r[0] for r in rows_] == ["user", "assistant"] and rows_[0][1] == "show me hoodies please"
    assert rows_[1][1] == message_of(msgs)["text"] and json.loads(rows_[1][2]) == message_of(msgs)["cards"]
    say(shop, headers, "thanks", conversation_id=created)  # the same, now known, conversation: no new `conversation` event
    msgs = turn(ws)
    assert "conversation" not in types(msgs) and len(saved(shop, "U-03", created)) == 4


def test_a_long_first_message_makes_a_60_character_title(make_shop):
    shop, _ = llm_shop(make_shop, says("ok"))
    headers, ws = open_chat(shop, "U-03")
    say(shop, headers, "a" * 100)
    cid = next(m for m in turn(ws) if m["type"] == "conversation")["data"]["conversation_id"]
    clients = shop.client.app.state.clients

    async def get():
        return await clients.call("assistant", clients.assistant.GetConversation, pb.GetConversationRequest(conversation_id=cid), user_id="U-03")

    assert shop.client.portal.call(get).title == "a" * 60


def test_guest_not_saved(make_shop):
    shop, llm = llm_shop(make_shop, uses(("search", {"category": "jackets"})), says("Sure."))
    ws = shop.ws(None, guest=GUEST)
    ws.get()
    say(shop, None, "show me jackets", guest_id=GUEST)
    msgs = turn(ws)
    assert "conversation" not in types(msgs) and message_of(msgs)["cards"][0]["kind"] == "products"
    assert "What you know about this person" not in llm.requests[0][1]["content"]
    say(shop, None, "and the second one?", guest_id=GUEST)  # still remembers the previous exchange within the visit
    turn(ws)
    assert [m["role"] for m in llm.requests[1]] == ["system", "system", "user", "assistant", "user"]
    assert llm.requests[1][2]["content"] == "show me jackets"


def test_signed_out_visitors_get_a_sign_in_card_for_the_cart(make_shop):
    shop, _ = llm_shop(make_shop, uses(("cart", {"action": "view"})))
    ws = shop.ws(None, guest=GUEST)
    assert ws.get()["data"]["user"] is None
    say(shop, None, "what's in my cart", guest_id=GUEST)
    message = message_of(turn(ws))
    assert message["cards"][0]["kind"] == "signin" and "sign in" in message["text"].lower()
    error(shop.client.post("/api/chat", json={"message": "hi"}), 422, "invalid")  # neither a session nor a guest id
    error(shop.client.post("/api/chat/proposals/PR-x/confirm", json={}), 401, "unauthenticated")


def test_follow_up_sees_latest_exchange(make_shop):
    shop, llm = llm_shop(make_shop, uses(("search", {"category": "hoodies"})), says("The second one is navy."))
    headers, ws = open_chat(shop, "U-03")
    say(shop, headers, "show me hoodies")
    msgs = turn(ws)
    cid = next(m for m in msgs if m["type"] == "conversation")["data"]["conversation_id"]
    products = message_of(msgs)["cards"][0]["data"]["products"]
    say(shop, headers, "tell me about the second one", conversation_id=cid)
    turn(ws)
    request = llm.requests[-1]
    assert [m["role"] for m in request] == ["system", "system", "user", "assistant", "user"]  # memory block, last exchange, new message
    assert request[2]["content"] == "show me hoodies" and request[-1]["content"] == "tell me about the second one"
    assert products[1]["name"] in request[3]["content"] and products[1]["product_id"] in request[3]["content"]


def test_memory_worker_gets_the_exchange_with_the_server_conversation_id(make_shop):
    shop, _ = llm_shop(make_shop, says("Nice to meet you."))
    submitted = []
    headers, ws = open_chat(shop, "U-03")
    shop.client.app.state.memory_worker.submit = lambda *a: submitted.append(a)
    say(shop, headers, "I'm Sam")
    cid = next(m for m in turn(ws) if m["type"] == "conversation")["data"]["conversation_id"]
    assert submitted == [("U-03", cid, "I'm Sam", "Nice to meet you.")]


def test_turn_logs_calls_and_tokens_without_content(make_shop, caplog):
    shop, _ = llm_shop(make_shop, uses(("cart", {"action": "view"}), usage=30), says("It is empty.", usage=12))
    headers, ws = open_chat(shop, "U-03")
    with caplog.at_level("INFO", logger="orchestrator.agent.loop"):
        say(shop, headers, "secret words here")
        turn(ws)
    assert [r.getMessage() for r in caplog.records if r.getMessage().startswith("chat turn ")] == ["chat turn llm_calls=2 tokens=42"]
    assert not any("secret" in r.getMessage() for r in caplog.records)


def restart(shop, llm):
    """A new AgentLoop over the same services: nothing in process memory survives, as after a restart or on a second instance."""
    from orchestrator.agent.loop import AgentLoop

    s = shop.client.app.state
    s.agent = AgentLoop(s.clients, s.hub, s.proposals, type(s.memory)(), llm, s.memory_reader, s.memory_worker)


def test_follow_up_after_a_restart_still_sees_the_numbered_list(make_shop):
    shop, llm = llm_shop(make_shop, uses(("search", {"category": "hoodies"})), says("It is navy."))
    headers, ws = open_chat(shop)
    say(shop, headers, "show me hoodies")
    msgs = turn(ws)
    cid = next(m for m in msgs if m["type"] == "conversation")["data"]["conversation_id"]
    products = message_of(msgs)["cards"][0]["data"]["products"]
    restart(shop, llm)
    say(shop, headers, "tell me about the second one", conversation_id=cid)
    turn(ws)
    shown = llm.requests[-1][3]["content"]
    assert f"2. {products[1]['name']} [{products[1]['product_id']}]" in shown and f"1. {products[0]['name']}" in shown


def test_remove_the_second_one_request_contains_the_cart_line_ids(make_shop):
    shop, llm = llm_shop(make_shop, uses(("cart", {"action": "view"})), says("ok"))
    headers, ws = open_chat(shop)
    first = sku_row()[3]
    second = next(s["sku_id"] for s in SKUS if s["sku_id"] != first and sku_with(4, 1) == s["sku_id"])
    shop.put(headers, first, 1)
    shop.put(headers, second, 1)
    say(shop, headers, "what is in my cart")
    msgs = turn(ws)
    cid = next(m for m in msgs if m["type"] == "conversation")["data"]["conversation_id"]
    restart(shop, llm)
    say(shop, headers, "remove the second one", conversation_id=cid)
    turn(ws)
    shown = llm.requests[-1][3]["content"]
    assert f"[{first}]" in shown and f"[{second}]" in shown
