"""Audit lines written by the services: one `event key=value ...` line per committed business change."""
import logging
import re
import time

import grpc
import pytest

from shopassist_proto import shopassist_pb2 as pb
from shopassist_proto import shopassist_pb2_grpc as pb_grpc
from tests import authchan
from tests.conftest import run
from tests.fixtures import DEMO_PASSWORD, EMAIL_OF
from tests.helpers import as_user, checkout, get_order, sku_with


@pytest.fixture(autouse=True)
def audit_on(caplog):
    caplog.set_level(logging.INFO, logger="audit")


def audit_lines(caplog):
    return [r.getMessage() for r in caplog.records if r.name == "audit"]


def wait_for(caplog, *events, wait: float = 2.0):
    """The audit lines so far, once one of each event has appeared (the server may log after it replies)."""
    deadline = time.monotonic() + wait
    while True:
        lines = audit_lines(caplog)
        if all(any(l.startswith(e + " ") or l == e for l in lines) for e in events) or time.monotonic() > deadline:
            return lines
        time.sleep(0.02)


def fields(line: str) -> dict[str, str]:
    return dict(re.findall(r'(\w+)=("(?:[^"\\]|\\.)*"|\S+)', line))


def named(lines, event):
    return [l for l in lines if l.split(" ")[0] == event]


def order_of_events(lines, *events):
    first = {e: next(i for i, l in enumerate(lines) if l.split(" ")[0] == e) for e in events}
    return [first[e] for e in events]


# ---------------- orders ----------------

def test_checkout_success_audit(make_stack, caplog):
    stack = make_stack()
    order = run(checkout(stack.order.addr, "U-01", "audit-ok", [(sku_with(4), 2)]))[-1].order
    wanted = ("checkout_started", "stock_reserved", "payment_charged", "order_placed", "order_confirmed")
    lines = wait_for(caplog, *wanted)
    positions = order_of_events(lines, *wanted)
    assert positions == sorted(positions)
    for event in wanted:
        (line,) = named(lines, event)
        assert fields(line)["order"] == order.order_id
    assert fields(named(lines, "checkout_started")[0])["total"] == f"${order.total_cents / 100:.2f}"
    assert fields(named(lines, "checkout_started")[0])["user"] == "U-01"
    assert fields(named(lines, "payment_charged")[0])["amount"] == f"${order.total_cents / 100:.2f}"
    assert fields(named(lines, "payment_charged")[0])["method"] == "card_ok"
    for event in wanted:
        assert fields(named(lines, event)[0])["user"] == "U-01", event


def test_replayed_checkout_logs_each_event_once(make_stack, caplog):
    stack = make_stack()
    sku = sku_with(4)
    first = run(checkout(stack.order.addr, "U-01", "audit-replay", [(sku, 1)]))[-1].order
    again = run(checkout(stack.order.addr, "U-01", "audit-replay", [(sku, 1)]))[-1].order
    assert again.order_id == first.order_id
    wanted = ("checkout_started", "stock_reserved", "payment_charged", "order_placed", "order_confirmed")
    lines = wait_for(caplog, *wanted)
    time.sleep(0.3)  # a wrongly repeated line would land about now
    lines = audit_lines(caplog)
    assert [len(named(lines, e)) for e in wanted] == [1] * 5


def test_checkout_payment_failure_audit(make_stack, caplog):
    stack = make_stack()
    order = run(checkout(stack.order.addr, "U-01", "audit-no", [(sku_with(4), 1)], method="card_declined"))[-1].order
    lines = wait_for(caplog, "payment_failed", "stock_released", "order_failed")
    assert order_of_events(lines, "payment_failed", "stock_released", "order_failed") == sorted(
        order_of_events(lines, "payment_failed", "stock_released", "order_failed"))
    for event in ("payment_failed", "stock_released", "order_failed"):
        assert fields(named(lines, event)[0])["order"] == order.order_id
    assert "card declined" in named(lines, "payment_failed")[0]
    assert not named(lines, "payment_charged") and not named(lines, "order_confirmed")
    for event in ("checkout_started", "stock_reserved", "payment_failed", "stock_released", "order_failed"):
        assert fields(named(lines, event)[0])["user"] == "U-01", event


def test_cancel_audit(make_stack, caplog):
    stack = make_stack()
    order = run(checkout(stack.order.addr, "U-01", "audit-c", [(sku_with(4), 1)]))[-1].order
    with authchan.channel(stack.order.addr) as ch:
        pb_grpc.OrderServiceStub(ch).CancelOrder(
            pb.CancelOrderRequest(order_id=order.order_id, idempotency_key="cancel-a"), metadata=as_user("U-01"))
    lines = wait_for(caplog, "cancel_requested", "order_cancelled", "refund_issued", "restock")
    for event in ("cancel_requested", "order_cancelled", "refund_issued"):
        (line,) = named(lines, event)
        assert fields(line)["order"] == order.order_id
    assert fields(named(lines, "cancel_requested")[0])["user"] == "U-01"
    assert fields(named(lines, "refund_issued")[0])["amount"] == f"${order.total_cents / 100:.2f}"
    assert named(lines, "restock")
    assert fields(named(lines, "refund_issued")[0])["user"] == "U-01"


# ---------------- carts ----------------

def set_line(stub, user, sku, qty, metadata=()):
    return stub.SetCartLine(pb.SetCartLineRequest(sku_id=sku, product_id="P-1", quantity=qty),
                            metadata=as_user(user) + tuple(metadata))


def test_cart_audit_source(fresh_customer_running, caplog):
    with authchan.channel(fresh_customer_running.addr) as ch:
        stub = pb_grpc.CustomerServiceStub(ch)
        set_line(stub, "U-03", "SKU-A", 2)
        set_line(stub, "U-03", "SKU-A", 5, metadata=(("x-source", "assistant"),))
        set_line(stub, "U-03", "SKU-A", 0)
        set_line(stub, "U-03", "SKU-A", 0)  # nothing to remove: no line
        set_line(stub, "U-03", "SKU-B", 1)
        stub.ClearCart(pb.ClearCartRequest(), metadata=as_user("U-03"))
        stub.ClearCart(pb.ClearCartRequest(), metadata=as_user("U-03"))  # already empty: no line
    lines = wait_for(caplog, "cart_cleared")
    cart = [l for l in lines if l.startswith("cart_")]
    assert len(cart) == 5
    assert cart[0].startswith("cart_line_set ") and fields(cart[0]) == {
        "user": "U-03", "sku": "SKU-A", "qty_before": "0", "qty_after": "2", "source": "web"}
    assert fields(cart[1]) == {"user": "U-03", "sku": "SKU-A", "qty_before": "2", "qty_after": "5", "source": "assistant"}
    assert cart[2].startswith("cart_line_removed ") and fields(cart[2])["qty_before"] == "5"
    assert cart[4].startswith("cart_cleared ") and fields(cart[4])["lines"] == "1" and fields(cart[4])["source"] == "web"


def test_login_failed_no_password(fresh_customer_running, caplog):
    email = EMAIL_OF["U-01"]
    with authchan.channel(fresh_customer_running.addr) as ch:
        stub = pb_grpc.CustomerServiceStub(ch)
        with pytest.raises(grpc.RpcError):
            stub.Authenticate(pb.AuthenticateRequest(email=email, password="hunter2-not-this"))
        stub.Authenticate(pb.AuthenticateRequest(email=email, password=DEMO_PASSWORD))
        stub.CreateUser(pb.CreateUserRequest(name="Ada Audit", email="ada.audit@example.test", password="s3cret-audit-pw"))
    lines = wait_for(caplog, "login_failed", "login_ok", "signup")
    assert fields(named(lines, "login_failed")[0])["email"] == email
    assert fields(named(lines, "login_ok")[0])["email"] == email
    assert fields(named(lines, "signup")[0])["email"] == "ada.audit@example.test"
    everything = "\n".join(r.getMessage() + " " + str(r.args) for r in caplog.records)
    assert "hunter2-not-this" not in everything and "s3cret-audit-pw" not in everything and DEMO_PASSWORD not in everything


# ---------------- assistant ----------------

def test_fact_audit(assistant, caplog):
    def change(*changes):
        return assistant.ApplyFacts(pb.ApplyFactsRequest(changes=list(changes)), metadata=as_user("U-01"))

    def remember(key, value, kind="permanent", category=""):
        return pb.FactChange(op="remember", kind=kind, key=key, value=value, category=category)

    conv = assistant.CreateConversation(pb.CreateConversationRequest(title="Trip planning"), metadata=as_user("U-01"))
    assistant.RenameConversation(pb.RenameConversationRequest(conversation_id=conv.conversation_id, title="Trip 2"), metadata=as_user("U-01"))
    assistant.DeleteConversation(pb.DeleteConversationRequest(conversation_id=conv.conversation_id), metadata=as_user("U-01"))
    change(remember("liked_colors", "navy"), remember("size_top", "M"))
    change(remember("size_top", "L"))
    change(remember("budget", "80", kind="temporal", category="trip"))
    change(pb.FactChange(op="forget", key="liked_colors", value="navy"))
    assistant.ClearTemporalFacts(pb.ClearTemporalFactsRequest(categories=["trip"]), metadata=as_user("U-01"))
    lines = wait_for(caplog, "conversation_deleted", "temporal_facts_cleared")
    assert [fields(l)["title"] for l in lines if l.startswith("conversation_")] == ['"Trip planning"', '"Trip 2"', '"Trip 2"']
    assert [l.split(" ")[0] for l in lines if l.startswith("conversation_")] == [
        "conversation_created", "conversation_renamed", "conversation_deleted"]
    added = [fields(l) for l in named(lines, "fact_added")]
    assert {"key": "liked_colors", "value": "navy", "kind": "permanent", "category": '""', "user": "U-01"} == {
        k: v for k, v in added[0].items()}
    replaced = fields(named(lines, "fact_replaced")[0])
    assert replaced["key"] == "size_top" and replaced["value"] == "L"
    assert fields(named(lines, "fact_removed")[0])["value"] == "navy"
    cleared = fields(named(lines, "temporal_facts_cleared")[0])
    assert cleared["count"] == "1" and cleared["categories"] == "trip" and cleared["user"] == "U-01"
    assert len(named(lines, "fact_added")) == 3 and len(named(lines, "fact_replaced")) == 1
