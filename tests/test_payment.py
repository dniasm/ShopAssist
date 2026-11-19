"""PaymentService: deterministic simulated charges, idempotent by key, refunds once per payment."""
import asyncio
import time

import grpc
import psycopg
import pytest

from shopassist_proto import shopassist_pb2 as pb
from shopassist_proto import shopassist_pb2_grpc as pb_grpc
from tests.conftest import run, start, stop


def charge(stub, key="charge:O-1", order="O-1", amount=5000, method="card_ok"):
    return stub.Charge(pb.ChargeRequest(idempotency_key=key, order_id=order, amount_cents=amount, payment_method=method))


def code_of(fn):
    with pytest.raises(grpc.RpcError) as e:
        fn()
    return e.value.code()


def rows(url, sql, params=()):
    with psycopg.connect(url) as conn:
        return conn.execute(sql, params).fetchall()


def test_card_ok_succeeds_and_card_declined_declines_with_a_reason(payment):
    ok = charge(payment)
    assert ok.status == pb.PAYMENT_SUCCEEDED and ok.payment_id.startswith("PAY-") and not ok.decline_reason
    no = charge(payment, key="charge:O-2", order="O-2", method="card_declined")
    assert no.status == pb.PAYMENT_DECLINED and no.decline_reason == "card declined by issuer" and no.payment_id


def test_card_flaky_is_decided_by_the_key_so_retries_get_the_same_answer(payment):
    first = {f"charge:O-{i}": charge(payment, key=f"charge:O-{i}", order=f"O-{i}", method="card_flaky").status for i in range(40)}
    again = {k: charge(payment, key=k, order=k.split(":")[1], method="card_flaky").status for k in first}
    assert first == again  # same key, same answer, every time
    declined = sum(1 for s in first.values() if s == pb.PAYMENT_DECLINED)
    assert 8 <= declined <= 32  # roughly half


def test_same_key_is_one_payment(payment, payment_running):
    a, b = charge(payment), charge(payment)
    assert a.payment_id == b.payment_id and a.status == b.status
    assert rows(payment_running.url, "SELECT count(*) FROM payments WHERE idempotency_key = 'charge:O-1'") == [(1,)]


def test_same_key_with_a_different_request_is_already_exists(payment):
    charge(payment)
    for kw in ({"amount": 5001}, {"order": "O-other"}, {"method": "card_declined"}):
        assert code_of(lambda: charge(payment, **kw)) == grpc.StatusCode.ALREADY_EXISTS


def test_charge_rejects_bad_input(payment):
    for kw in ({"key": ""}, {"order": ""}, {"amount": 0}, {"amount": -5}, {"method": "bitcoin"}, {"method": ""}):
        assert code_of(lambda: charge(payment, **kw)) == grpc.StatusCode.INVALID_ARGUMENT


def test_ten_concurrent_charges_with_one_key_make_one_payment(payment_running):
    async def scenario():
        async with grpc.aio.insecure_channel(payment_running.addr) as ch:
            stub = pb_grpc.PaymentServiceStub(ch)
            req = pb.ChargeRequest(idempotency_key="charge:O-C", order_id="O-C", amount_cents=900, payment_method="card_ok")
            return {r.payment_id for r in await asyncio.gather(*(stub.Charge(req) for _ in range(10)))}

    assert len(run(scenario())) == 1
    assert rows(payment_running.url, "SELECT count(*) FROM payments WHERE order_id = 'O-C'") == [(1,)]


def test_latency_is_applied(loop_thread, temp_dbs, seed_dir):
    from services.payment.server import create_service
    r = start(loop_thread, temp_dbs, "payment", create_service, seed_dir=seed_dir, latency_ms=250)
    try:
        with grpc.insecure_channel(r.addr) as ch:
            started = time.monotonic()
            charge(pb_grpc.PaymentServiceStub(ch))
            assert time.monotonic() - started >= 0.25
    finally:
        stop(loop_thread, temp_dbs, r)


# ---------------- refunds ----------------

def refund(stub, payment_id, key="refund:O-1", amount=5000):
    return stub.Refund(pb.RefundRequest(idempotency_key=key, payment_id=payment_id, amount_cents=amount))


def test_refund_marks_the_payment_refunded_once_per_key(payment, payment_running):
    paid = charge(payment)
    first, again = refund(payment, paid.payment_id), refund(payment, paid.payment_id)
    assert first.status == pb.PAYMENT_REFUNDED and first.refund_id.startswith("RF-") and first.refund_id == again.refund_id
    assert rows(payment_running.url, "SELECT status FROM payments WHERE payment_id = %s", (paid.payment_id,)) == [("REFUNDED",)]
    assert rows(payment_running.url, "SELECT count(*) FROM refunds") == [(2,)]  # one seeded + this one


def test_refund_rules(payment):
    paid = charge(payment)
    assert code_of(lambda: refund(payment, paid.payment_id, amount=5001)) == grpc.StatusCode.INVALID_ARGUMENT  # more than charged
    assert code_of(lambda: refund(payment, paid.payment_id, amount=0)) == grpc.StatusCode.INVALID_ARGUMENT
    assert code_of(lambda: refund(payment, "PAY-nope")) == grpc.StatusCode.NOT_FOUND
    declined = charge(payment, key="charge:O-3", order="O-3", method="card_declined")
    assert code_of(lambda: refund(payment, declined.payment_id, key="refund:O-3")) == grpc.StatusCode.FAILED_PRECONDITION
    refund(payment, paid.payment_id)
    assert code_of(lambda: refund(payment, paid.payment_id, key="refund:another")) == grpc.StatusCode.FAILED_PRECONDITION  # already refunded
    assert code_of(lambda: refund(payment, paid.payment_id, amount=4000)) == grpc.StatusCode.ALREADY_EXISTS  # same key, other request


# Invariant: a payment can be refunded at most once, however many refund attempts race.
def test_concurrent_refunds_of_one_payment_succeed_exactly_once(payment, payment_running):
    paid = charge(payment)

    async def scenario():
        async with grpc.aio.insecure_channel(payment_running.addr) as ch:
            stub = pb_grpc.PaymentServiceStub(ch)
            results = await asyncio.gather(*(
                stub.Refund(pb.RefundRequest(idempotency_key=f"refund:try-{i}", payment_id=paid.payment_id, amount_cents=5000))
                for i in range(10)
            ), return_exceptions=True)
            return [r for r in results if not isinstance(r, Exception)]

    assert len(run(scenario())) == 1
    assert rows(payment_running.url, "SELECT count(*) FROM refunds WHERE payment_id = %s", (paid.payment_id,)) == [(1,)]


def test_seeded_payments_are_loaded_with_their_refunds(payment_running):
    assert rows(payment_running.url, "SELECT payment_id, order_id, amount_cents, status FROM payments ORDER BY payment_id") == [
        ("PAY-10001", "O-10001", 6000, "SUCCEEDED"), ("PAY-10002", "O-10002", 6000, "REFUNDED"),
    ]
    assert rows(payment_running.url, "SELECT payment_id FROM refunds") == [("PAY-10002",)]


# ---------------- LookupCharge: read-only, so recovery can LEARN what happened without ever charging ----------------

def lookup(stub, key):
    return stub.LookupCharge(pb.LookupChargeRequest(idempotency_key=key))


def test_lookup_returns_the_stored_result_of_a_charge_and_changes_nothing(payment, payment_running):
    ok = charge(payment)
    no = charge(payment, key="charge:O-2", order="O-2", method="card_declined")
    before = rows(payment_running.url, "SELECT count(*) FROM payments")
    found = lookup(payment, "charge:O-1")
    assert (found.payment_id, found.status, found.decline_reason) == (ok.payment_id, pb.PAYMENT_SUCCEEDED, "")
    declined = lookup(payment, "charge:O-2")
    assert (declined.payment_id, declined.status, declined.decline_reason) == (no.payment_id, pb.PAYMENT_DECLINED, "card declined by issuer")
    assert rows(payment_running.url, "SELECT count(*) FROM payments") == before


def test_lookup_of_a_key_that_never_reached_payment_is_not_found_and_creates_nothing(payment, payment_running):
    before = rows(payment_running.url, "SELECT count(*) FROM payments")
    assert code_of(lambda: lookup(payment, "charge:O-never")) == grpc.StatusCode.NOT_FOUND
    assert code_of(lambda: lookup(payment, "")) == grpc.StatusCode.INVALID_ARGUMENT
    assert rows(payment_running.url, "SELECT count(*) FROM payments") == before  # unlike Charge, looking does not do


def test_lookup_reports_a_refunded_payment_as_refunded(payment):
    paid = charge(payment)
    refund(payment, paid.payment_id)
    assert lookup(payment, "charge:O-1").status == pb.PAYMENT_REFUNDED
