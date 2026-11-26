"""The fulfilment worker: walks confirmed orders through packing, shipping and delivery, one step per due time.

Safe with several order-service processes on one database: due orders are picked with FOR UPDATE SKIP LOCKED
(each worker gets different rows) and every move goes through `change_status`, whose status guard turns a repeat
into a no-op. Orders being cancelled (`saga_step = 'CANCELLING'`) and seeded history (`tracked = false`) are skipped.
"""
import asyncio
import logging

from psycopg_pool import AsyncConnectionPool

from services.order import db
from shared_common.common.log import set_request_id

log = logging.getLogger(__name__)

# status -> (next status, message the customer sees). No customer lookup here: the worker only knows the order.
NEXT = {
    "CONFIRMED": ("PACKED", "Packed at the warehouse"),
    "PACKED": ("SHIPPED", "Handed to the courier"),
    "SHIPPED": ("OUT_FOR_DELIVERY", "Out for delivery today"),
    "OUT_FOR_DELIVERY": ("DELIVERED", "Delivered. Enjoy!"),
}


async def advance_due(pool: AsyncConnectionPool, step_seconds: float, batch: int = 50) -> int:
    """Move every due order ONE step; returns how many moved. One transaction: no remote calls happen inside it."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT order_id, status FROM orders WHERE tracked AND saga_step = 'DONE' AND status = ANY(%s)"
            " AND updated_at + make_interval(secs => %s) <= now() ORDER BY updated_at LIMIT %s FOR UPDATE SKIP LOCKED",
            (list(NEXT), step_seconds, batch),
        )
        moved = 0
        for order in await cur.fetchall():
            next_status, message = NEXT[order["status"]]
            if await db.change_status(conn, order["order_id"], (order["status"],), next_status, message):
                moved += 1
                log.info("order %s %s -> %s", order["order_id"], order["status"], next_status)
        return moved


async def fulfilment_loop(pool: AsyncConnectionPool, step_seconds: float, poll_seconds: float) -> None:
    """Every `poll_seconds`; a failed pass is logged and retried."""
    while True:
        set_request_id()  # one trace per pass
        try:
            if moved := await advance_due(pool, step_seconds):
                log.info("advanced %d order(s)", moved)
        except Exception:
            log.exception("fulfilment pass failed")
        await asyncio.sleep(poll_seconds)
