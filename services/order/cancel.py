"""Cancelling a confirmed order: put the stock back, refund the card, then mark it CANCELLED.

Crash-safe the same way as checkout. The claim (`saga_step = 'CANCELLING'`, taken under the order's row lock)
comes first and is what stops the fulfilment worker from shipping the order meanwhile; every later step is an
idempotent write keyed by the order id, so recovery can simply run the remaining steps again.
"""
import logging

from services.order import db
from services.order.saga import Deps, crash_point, remote_call
from shared_common.common.log import audit
from shopassist_proto import shopassist_pb2 as pb

log = logging.getLogger(__name__)
CANCELLABLE = ("CONFIRMED", "PACKED")
CANCELLED_MESSAGE = "Order cancelled by the customer; payment refunded."


class OrderNotFound(Exception):
    pass


class NotCancellable(Exception):
    """The order's current state does not allow cancelling. str(e) names that state."""


async def claim(pool, user_id: str, order_id: str, key: str) -> str:
    """Take the cancellation of an order: 'claimed' (we start), 'resume' (this key already started it), 'done' (finished).

    The order row is locked while we decide, so a concurrent fulfilment step or cancel sees either the state before
    or after us, never between.
    """
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT status, saga_step, cancel_key FROM orders WHERE order_id = %s AND user_id = %s FOR UPDATE", (order_id, user_id)
        )
        row = await cur.fetchone()
        if row is None:
            raise OrderNotFound(order_id)
        if row["status"] == "CANCELLED":
            if row["cancel_key"] == key:
                return "done"
            raise NotCancellable("order is already CANCELLED")
        if row["saga_step"] == "CANCELLING":
            if row["cancel_key"] == key:
                return "resume"
            raise NotCancellable("another cancellation of this order is in progress")
        if row["status"] in CANCELLABLE and row["saga_step"] == "DONE":
            await conn.execute(
                "UPDATE orders SET saga_step = 'CANCELLING', cancel_key = %s, updated_at = now() WHERE order_id = %s", (key, order_id)
            )
            return "claimed"
        raise NotCancellable(f"order is {row['status']}; only CONFIRMED or PACKED orders can be cancelled")


class Canceller:
    def __init__(self, deps: Deps):
        self.deps = deps

    async def cancel(self, user_id: str, order_id: str, key: str) -> dict:
        """Cancel the caller's order (idempotent by key) and return it. Raises OrderNotFound / NotCancellable."""
        outcome = await claim(self.deps.pool, user_id, order_id, key)
        if outcome == "claimed":
            audit("cancel_requested", user=user_id, order=order_id)
        if outcome != "done":
            await self.finish(await db.load_order(self.deps.pool, order_id, history=False))
        return await db.load_order(self.deps.pool, order_id)

    async def finish(self, order: dict, recovering: bool = False) -> None:
        """The steps after the claim: restock, refund, CANCELLED. All idempotent, so recovery just calls this again."""
        order_id, timeout = order["order_id"], self.deps.call_timeout
        lines = [pb.StockLine(sku_id=l["sku_id"], quantity=l["quantity"]) for l in order["lines"]]
        await remote_call(self.deps.inventory.Restock, pb.RestockRequest(idempotency_key=f"cancel:{order_id}", lines=lines), timeout)
        crash_point(self.deps, "cancel_after_restock", recovering)
        if order["payment_id"]:
            await remote_call(
                self.deps.payment.Refund,
                pb.RefundRequest(idempotency_key=f"refund:{order_id}", payment_id=order["payment_id"], amount_cents=order["total_cents"]),
                timeout, user_id=order["user_id"],
            )
        async with self.deps.pool.connection() as conn:
            changed = await db.change_status(conn, order_id, CANCELLABLE, "CANCELLED", CANCELLED_MESSAGE)
        if changed:
            audit("order_cancelled", user=order["user_id"], order=order_id, reason="cancelled by customer")
