"""Saved chats over REST (list, open, rename, delete) and the checkout-redirect confirm. Real assistant service; no LLM needed
except for the redirect proposal."""
import json

import pytest

from shopassist_proto import shopassist_pb2 as pb
from tests.test_agent_chat import FakeLLM, advance, count_calls, make_shop, open_chat, place_order, say, sku_row, turn, uses  # noqa: F401
from tests.test_api_shop import error
from tests.test_api_shop import make_shop as shop_factory  # noqa: F401  (fixture shared with the shop tests)
from tests.helpers import rows


def assistant_call(shop, user, method, request):
    clients = shop.client.app.state.clients

    async def go():
        return await clients.call("assistant", getattr(clients.assistant, method), request, user_id=user)

    return shop.client.portal.call(go)


def new_chat(shop, user, title="Trip", messages=()):
    conversation = assistant_call(shop, user, "CreateConversation", pb.CreateConversationRequest(title=title))
    for role, text, cards in messages:
        assistant_call(shop, user, "AppendMessage", pb.AppendMessageRequest(
            conversation_id=conversation.conversation_id, role=role, text=text, cards_json=cards))
    return conversation.conversation_id


def test_list_open_rename_delete_happy_path(make_shop):
    shop = make_shop()
    headers, _ = shop.login("U-03")
    cards = json.dumps([{"kind": "order", "data": {"x": 1}}])
    first = new_chat(shop, "U-03", "First", [("user", "hi", ""), ("assistant", "hello", cards), ("user", "bye", "\"not a list\"")])
    second = new_chat(shop, "U-03", "Second")
    listed = shop.client.get("/api/chat/conversations", headers=headers).json()
    assert [c["conversation_id"] for c in listed] == [second, first] and set(listed[0]) == {"conversation_id", "title", "updated_at"}
    messages = shop.client.get(f"/api/chat/conversations/{first}/messages", headers=headers).json()
    assert [(m["role"], m["text"], m["cards"]) for m in messages] == [
        ("user", "hi", []), ("assistant", "hello", [{"kind": "order", "data": {"x": 1}}]), ("user", "bye", [])]
    assert set(messages[0]) == {"role", "text", "cards", "created_at"}
    renamed = shop.client.patch(f"/api/chat/conversations/{first}", json={"title": "  Renamed  "}, headers=headers)
    assert renamed.status_code == 200 and renamed.json()["title"] == "Renamed" and renamed.json()["conversation_id"] == first
    assert shop.client.delete(f"/api/chat/conversations/{first}", headers=headers).json() == {"ok": True}
    assert [c["conversation_id"] for c in shop.client.get("/api/chat/conversations", headers=headers).json()] == [second]
    error(shop.client.get(f"/api/chat/conversations/{first}/messages", headers=headers), 404, "not_found")


@pytest.mark.parametrize("title", ["", "   ", "x" * 81])
def test_rename_validates_the_title(make_shop, title):
    shop = make_shop()
    headers, _ = shop.login("U-03")
    conversation_id = new_chat(shop, "U-03")
    assert shop.client.patch(f"/api/chat/conversations/{conversation_id}", json={"title": title}, headers=headers).status_code == 422
    assert shop.client.patch(f"/api/chat/conversations/{conversation_id}", json={"title": "x" * 80}, headers=headers).status_code == 200


def test_signed_out_is_401(make_shop):
    shop = make_shop()
    assert shop.client.get("/api/chat/conversations").status_code == 401
    assert shop.client.delete("/api/chat/conversations/abc").status_code == 401


def test_other_users_conversation_404(make_shop):
    shop = make_shop()
    mine, _ = shop.login("U-03")
    theirs, _ = shop.login("U-04")
    conversation_id = new_chat(shop, "U-03", messages=[("user", "secret", "")])
    c = shop.client
    error(c.get(f"/api/chat/conversations/{conversation_id}/messages", headers=theirs), 404, "not_found")
    error(c.patch(f"/api/chat/conversations/{conversation_id}", json={"title": "mine now"}, headers=theirs), 404, "not_found")
    error(c.delete(f"/api/chat/conversations/{conversation_id}", headers=theirs), 404, "not_found")
    error(c.get("/api/chat/conversations/nope/messages", headers=theirs), 404, "not_found")
    assert c.get("/api/chat/conversations", headers=theirs).json() == []
    assert c.get(f"/api/chat/conversations/{conversation_id}/messages", headers=mine).json()[0]["text"] == "secret"


def redirect_proposal(make_shop, user):
    shop = make_shop(llm=FakeLLM(uses(("checkout", {}))))
    headers, ws = open_chat(shop, user)
    shop.put(headers, sku_row()[3], 1)
    say(shop, headers, "pay")
    msgs = turn(ws)
    return shop, headers, ws, next(m for m in msgs if m["type"] == "proposal")["data"]["proposal_id"]


def test_confirm_checkout_redirect_navigates_only(make_shop):
    shop, headers, ws, proposal_id = redirect_proposal(make_shop, "U-05")
    checkouts = count_calls(shop, "Checkout")
    r = shop.client.post(f"/api/chat/proposals/{proposal_id}/confirm", headers=headers)
    assert r.status_code == 202 and r.json() == {"ok": True, "path": "/checkout"}  # the ack is what navigates the tab
    msgs = ws.until("message")
    assert "navigate" not in [m["type"] for m in msgs]  # not twice: no socket navigate for a confirm
    assert msgs[-1]["data"]["text"] == "Taking you to checkout." and msgs[-1]["data"]["cards"] == []
    assert checkouts == [] and rows(shop.stack.order.url, "SELECT count(*) FROM orders WHERE order_id > 'O-2'") == [(0,)]
    error(shop.client.post(f"/api/chat/proposals/{proposal_id}/confirm", headers=headers), 409, "conflict")


def test_confirm_unknown_action_is_invalid_and_released(make_shop):
    shop, headers, ws, proposal_id = redirect_proposal(make_shop, "U-06")
    shop.client.app.state.proposals._items[proposal_id].action = "mystery"
    error(shop.client.post(f"/api/chat/proposals/{proposal_id}/confirm", headers=headers), 422, "invalid")
    shop.client.app.state.proposals._items[proposal_id].action = "checkout_redirect"
    assert shop.client.post(f"/api/chat/proposals/{proposal_id}/confirm", headers=headers).status_code == 202  # released, so it can be retried


# ---------------- a pending proposal survives reopening the chat and a restart ----------------

def reopened(shop, headers):
    """The proposal cards of the first saved chat, as the history endpoint returns them."""
    chat = shop.client.get("/api/chat/conversations", headers=headers).json()[0]["conversation_id"]
    messages = shop.client.get(f"/api/chat/conversations/{chat}/messages", headers=headers).json()
    return [c["data"] for m in messages for c in m["cards"] if c["kind"] == "proposal"]


def restart(shop):
    """A fresh proposal store, as after an orchestrator restart (same secret)."""
    from orchestrator.agent.state import ProposalStore
    shop.client.app.state.proposals = ProposalStore(shop.client.app.state.settings.session_secret)


def test_reopened_chat_offers_the_pending_proposal_and_it_can_be_confirmed_once(make_shop):
    shop, headers, ws, proposal_id = redirect_proposal(make_shop, "U-07")
    (saved_proposal,) = reopened(shop, headers)
    assert saved_proposal["proposal_id"] == proposal_id and saved_proposal["available"] is True
    assert saved_proposal["action"] == "checkout_redirect" and saved_proposal["summary"]
    checkouts = count_calls(shop, "Checkout")
    assert shop.client.post(f"/api/chat/proposals/{proposal_id}/confirm", headers=headers).status_code == 202
    assert checkouts == []  # only navigates
    error(shop.client.post(f"/api/chat/proposals/{proposal_id}/confirm", headers=headers), 409, "conflict")
    assert reopened(shop, headers)[0]["available"] is False


def test_proposal_confirms_after_an_orchestrator_restart(make_shop):
    shop, headers, ws, proposal_id = redirect_proposal(make_shop, "U-08")
    restart(shop)
    assert reopened(shop, headers)[0]["available"] is True
    r = shop.client.post(f"/api/chat/proposals/{proposal_id}/confirm", headers=headers)
    assert r.status_code == 202 and r.json() == {"ok": True, "path": "/checkout"}  # navigates from the ack even with no turn on screen
    error(shop.client.post(f"/api/chat/proposals/{proposal_id}/confirm", headers=headers), 409, "conflict")


def test_restored_proposal_belongs_to_its_owner_and_cannot_be_forged_or_extended(make_shop):
    shop, headers, ws, proposal_id = redirect_proposal(make_shop, "U-09")
    other, _ = shop.login("U-10")
    restart(shop)
    error(shop.client.post(f"/api/chat/proposals/{proposal_id}/confirm", headers=other), 404, "not_found")
    error(shop.client.post(f"/api/chat/proposals/{proposal_id[:-2]}xx/confirm", headers=headers), 404, "not_found")
    assert shop.client.post(f"/api/chat/proposals/{proposal_id}/confirm", headers=headers).status_code == 202


def test_expired_proposal_shows_as_unavailable_after_restart_and_cannot_be_confirmed(make_shop):
    shop, headers, ws, proposal_id = redirect_proposal(make_shop, "U-11")
    restart(shop)
    shop.client.app.state.proposals._clock = lambda: 4_000_000_000  # long after the ten minutes
    assert reopened(shop, headers)[0]["available"] is False
    error(shop.client.post(f"/api/chat/proposals/{proposal_id}/confirm", headers=headers), 404, "not_found")


def test_proposal_is_unavailable_when_the_cart_has_been_emptied(make_shop):
    shop, headers, ws, proposal_id = redirect_proposal(make_shop, "U-12")
    shop.put(headers, sku_row()[3], 0)
    assert reopened(shop, headers)[0]["available"] is False
    error(shop.client.post(f"/api/chat/proposals/{proposal_id}/confirm", headers=headers), 409, "conflict")


def test_cancel_proposal_survives_restart_and_is_unavailable_once_the_order_is_cancelled(make_shop):
    shop = make_shop()
    headers, ws = open_chat(shop, "U-03")
    order_id = place_order(shop, headers, ws, "c1")
    advance(shop.stack)  # PACKED
    shop.client.app.state.agent.llm = FakeLLM(uses(("cancel_order", {"order_id": order_id})))
    say(shop, headers, f"cancel {order_id}")
    proposal_id = next(m for m in turn(ws) if m["type"] == "proposal")["data"]["proposal_id"]
    restart(shop)
    (card,) = reopened(shop, headers)
    assert card["available"] is True and card["args"] == {"order_id": order_id}
    assert shop.client.post(f"/api/chat/proposals/{proposal_id}/confirm", headers=headers).status_code == 202
    assert shop.client.get(f"/api/orders/{order_id}", headers=headers).json()["order"]["status"] == "CANCELLED"
    restart(shop)  # the "used" mark is gone, the order state still says no
    assert reopened(shop, headers)[0]["available"] is False
    error(shop.client.post(f"/api/chat/proposals/{proposal_id}/confirm", headers=headers), 409, "conflict")
