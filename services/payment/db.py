"""Payment SQL. Charge and Refund are each one transaction; the key's UNIQUE constraint is what makes them idempotent."""
import hashlib

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from services.common.idempotency import check_replay, fingerprint

METHODS = ("card_ok", "card_declined", "card_flaky")
DECLINE_REASON = "card declined by issuer"


class PaymentNotFound(Exception):
    pass


class NotRefundable(Exception):
    """The payment did not succeed, or has already been refunded."""

    def __init__(self, message: str, order_id: str = ""):
        super().__init__(message)
        self.order_id = order_id  # for the audit line


class AmountTooLarge(Exception):
    def __init__(self, message: str, order_id: str = ""):
        super().__init__(message)
        self.order_id = order_id


def decide(method: str, idempotency_key: str) -> tuple[str, str]:
    """(status, decline_reason). `card_flaky` hashes the key, so a retry of the same charge gets the same answer."""
    if method == "card_declined":
        return "DECLINED", DECLINE_REASON
    if method == "card_flaky" and hashlib.sha256(idempotency_key.encode()).digest()[0] % 2 == 0:
        return "DECLINED", DECLINE_REASON
    return "SUCCEEDED", ""


async def load_seed(conn: AsyncConnection, payments: list[dict]) -> int:
    """Historic payments from payments.json; refunded ones also get their refund row."""
    for p in payments:
        status = p["status"].removeprefix("PAYMENT_")
        await conn.execute(
            "INSERT INTO payments (payment_id, idempotency_key, order_id, amount_cents, payment_method, status)"
            " VALUES (%s, %s, %s, %s, 'card_ok', %s)",
            (p["payment_id"], f"charge:{p['order_id']}", p["order_id"], p["amount_cents"], status),
        )
        if status == "REFUNDED":
            await conn.execute(
                "INSERT INTO refunds (refund_id, idempotency_key, payment_id, amount_cents) VALUES (%s, %s, %s, %s)",
                ("RF-" + p["payment_id"].split("-")[1], f"refund:{p['order_id']}", p["payment_id"], p["amount_cents"]),
            )
    return len(payments)


async def charge(pool: AsyncConnectionPool, key: str, order_id: str, amount_cents: int, method: str) -> dict:
    """Record the charge once per key; a replay returns the stored row, a replay of a different request raises KeyReused."""
    fp = fingerprint({"order_id": order_id, "amount_cents": amount_cents, "method": method})
    status, reason = decide(method, key)
    async with pool.connection() as conn:
        cur = await conn.execute(
            "INSERT INTO payments (idempotency_key, fingerprint, order_id, amount_cents, payment_method, status, decline_reason)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (idempotency_key) DO NOTHING"
            " RETURNING payment_id, status, decline_reason",
            (key, fp, order_id, amount_cents, method, status, reason),
        )
        row = await cur.fetchone()
        if row is not None:
            return {**row, "created": True}  # created: this call made the charge (a replay returns the stored one)
        cur = await conn.execute(
            "SELECT payment_id, status, decline_reason, fingerprint FROM payments WHERE idempotency_key = %s", (key,)
        )
        stored = await cur.fetchone()
        check_replay(key, stored["fingerprint"], fp)
        return stored


async def lookup_charge(pool: AsyncConnectionPool, key: str) -> dict | None:
    """What a charge key produced, or None if the key never reached us. Read-only: it can never start a charge."""
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT payment_id, status, decline_reason FROM payments WHERE idempotency_key = %s", (key,))
        return await cur.fetchone()


async def refund(pool: AsyncConnectionPool, key: str, payment_id: str, amount_cents: int) -> dict:
    """Refund a SUCCEEDED payment once. The payment row is locked, so racing refunds are decided one at a time."""
    fp = fingerprint({"payment_id": payment_id, "amount_cents": amount_cents})
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT status, amount_cents, order_id FROM payments WHERE payment_id = %s FOR UPDATE", (payment_id,))
        payment = await cur.fetchone()
        if payment is None:
            raise PaymentNotFound(payment_id)
        cur = await conn.execute("SELECT refund_id, fingerprint FROM refunds WHERE idempotency_key = %s", (key,))
        replay = await cur.fetchone()
        if replay is not None:
            check_replay(key, replay["fingerprint"], fp)
            return {"refund_id": replay["refund_id"]}
        if payment["status"] != "SUCCEEDED":
            raise NotRefundable(f"payment {payment_id} is {payment['status'].lower()}, so it cannot be refunded", payment["order_id"])
        if amount_cents > payment["amount_cents"]:
            raise AmountTooLarge(f"cannot refund {amount_cents}: only {payment['amount_cents']} was charged", payment["order_id"])
        cur = await conn.execute(
            "INSERT INTO refunds (idempotency_key, fingerprint, payment_id, amount_cents) VALUES (%s, %s, %s, %s)"
            " RETURNING refund_id",
            (key, fp, payment_id, amount_cents),
        )
        refund_row = await cur.fetchone()
        await conn.execute("UPDATE payments SET status = 'REFUNDED', updated_at = now() WHERE payment_id = %s", (payment_id,))
    return {**refund_row, "order_id": payment["order_id"], "created": True}
