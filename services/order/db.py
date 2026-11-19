"""Order SQL. Every status change goes through `change_status`: it updates the order, writes the outbox row and
NOTIFYs, all in the caller's transaction. Nothing else may touch `orders.status` (tests/test_order_events.py
checks the source), so the event log can never disagree with the orders.

The `WHERE status IN (...)` guard inside `change_status` makes every change idempotent and race-safe: the live saga
and recovery, a cancel and the fulfilment worker may all try to move one order, and exactly one of them does.
"""
import json

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from services.common.idempotency import check_replay

PLACED_MESSAGE = "Order placed; checking out."
CONFIRMED_MESSAGE = "Order confirmed and payment received."


async def load_seed(
    conn: AsyncConnection, orders: list[dict], payment_ids: dict[str, str], categories: dict[str, str]
) -> int:
    """Historic orders (orders.json), their lines and events.

    `payment_ids` maps order_id -> payment_id (payments.json); `categories` maps sku_id -> category (catalog.json).
    """
    for o in orders:
        await conn.execute(
            "INSERT INTO orders (order_id, user_id, status, saga_step, payment_id, subtotal_cents, shipping_cents,"
            " total_cents, created_at, updated_at, tracked) VALUES (%s, %s, %s, 'DONE', %s, %s, %s, %s, %s, %s, false)",
            (o["order_id"], o["user_id"], o["status"], payment_ids.get(o["order_id"], ""), o["subtotal_cents"],
             o["shipping_cents"], o["total_cents"], o["created_at"], o["updated_at"]),
        )
        for line in o["lines"]:
            await conn.execute(
                "INSERT INTO order_lines (order_id, sku_id, product_id, name, size, color, quantity, unit_price_cents, category)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (o["order_id"], line["sku_id"], line["product_id"], line["name"], line["size"], line["color"],
                 line["quantity"], line["unit_price_cents"], categories.get(line["sku_id"])),
            )
    events = sorted(((e["timestamp"], o["order_id"], o["user_id"], e) for o in orders for e in o["events"]))
    for _, order_id, user_id, e in events:  # oldest first, so event ids increase with time
        await conn.execute(
            "INSERT INTO order_events (order_id, user_id, status, message, created_at) VALUES (%s, %s, %s, %s, %s)",
            (order_id, user_id, e["status"], e["message"], e["timestamp"]),
        )
    return len(orders)


async def backfill_categories(pool: AsyncConnectionPool, categories: dict[str, str]) -> int:
    """Give order lines that have no category one, from the catalog seed (history from before the column existed)."""
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT DISTINCT sku_id FROM order_lines WHERE category IS NULL")
        missing = [(categories[r["sku_id"]], r["sku_id"]) for r in await cur.fetchall() if r["sku_id"] in categories]
        async with conn.cursor() as c:
            await c.executemany("UPDATE order_lines SET category = %s WHERE sku_id = %s AND category IS NULL", missing)
        return len(missing)


async def begin_checkout(pool: AsyncConnectionPool, user_id: str, key: str, fp: str, payment_method: str) -> tuple[str, bool]:
    """Create the PENDING order for (user, key), or find the one that key already made. Returns (order_id, created).

    The order is inserted first and rolled back if the key turns out to exist, so there is never an order without a key.
    A repeat with another fingerprint raises KeyReused.
    """
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT 'O-' || nextval('order_seq') AS order_id")
        order_id = (await cur.fetchone())["order_id"]
        await conn.execute(
            "INSERT INTO orders (order_id, user_id, status, payment_method, tracked) VALUES (%s, %s, 'PENDING', %s, true)",
            (order_id, user_id, payment_method),
        )
        await _event(conn, order_id, user_id, "PENDING", PLACED_MESSAGE)
        cur = await conn.execute(
            "INSERT INTO checkout_keys (user_id, idempotency_key, fingerprint, order_id) VALUES (%s, %s, %s, %s)"
            " ON CONFLICT DO NOTHING RETURNING order_id",
            (user_id, key, fp, order_id),
        )
        if await cur.fetchone():
            return order_id, True
        await conn.rollback()  # the key was already used: forget the order we just made
        cur = await conn.execute(
            "SELECT order_id, fingerprint FROM checkout_keys WHERE user_id = %s AND idempotency_key = %s", (user_id, key)
        )
        stored = await cur.fetchone()
        check_replay(key, stored["fingerprint"], fp)
        return stored["order_id"], False


async def find_checkout(pool: AsyncConnectionPool, user_id: str, key: str) -> str | None:
    """The order this user's key already produced, if any (used to replay a checkout whose cart is gone)."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT order_id FROM checkout_keys WHERE user_id = %s AND idempotency_key = %s", (user_id, key)
        )
        row = await cur.fetchone()
        return row["order_id"] if row else None


async def _event(conn: AsyncConnection, order_id: str, user_id: str, status: str, message: str) -> int:
    """Write one outbox row and NOTIFY its id. pg_notify is transactional: listeners hear it only if this commits."""
    cur = await conn.execute(
        "INSERT INTO order_events (order_id, user_id, status, message) VALUES (%s, %s, %s, %s) RETURNING event_id",
        (order_id, user_id, status, message),
    )
    event_id = (await cur.fetchone())["event_id"]
    await conn.execute("SELECT pg_notify('order_events', %s)", (str(event_id),))
    return event_id


async def change_status(
    conn: AsyncConnection, order_id: str, from_statuses: tuple[str, ...], to_status: str, message: str,
    reason: str | None = None, shortages: list | None = None,
) -> bool:
    """THE way an order's status changes: update + outbox event + NOTIFY, in the caller's transaction.

    Returns False (and writes nothing) if the order is not currently in one of `from_statuses`, which is how a lost
    race looks. `reason`/`shortages` are kept as they are when None.
    """
    cur = await conn.execute(
        "UPDATE orders SET status = %s, saga_step = 'DONE', failure_reason = COALESCE(%s, failure_reason),"
        " shortages = COALESCE(%s::jsonb, shortages), updated_at = now()"
        " WHERE order_id = %s AND status = ANY(%s) RETURNING user_id",
        (to_status, reason, json.dumps(shortages) if shortages is not None else None, order_id, list(from_statuses)),
    )
    row = await cur.fetchone()
    if row is None:
        return False
    await _event(conn, order_id, row["user_id"], to_status, message)
    return True


async def set_step(pool: AsyncConnectionPool, order_id: str, step: str) -> None:
    """Record where the saga is (write-ahead: call this BEFORE the remote call the step names)."""
    async with pool.connection() as conn:
        await conn.execute("UPDATE orders SET saga_step = %s, updated_at = now() WHERE order_id = %s", (step, order_id))


async def save_priced_lines(pool: AsyncConnectionPool, order_id: str, lines: list[dict], totals: dict, next_step: str) -> None:
    """Validation outcome: the authoritative lines and totals, plus the next step, in one write."""
    async with pool.connection() as conn:
        for line in lines:
            await conn.execute(
                "INSERT INTO order_lines (order_id, sku_id, product_id, name, size, color, quantity, unit_price_cents, category)"
                " VALUES (%(order_id)s, %(sku_id)s, %(product_id)s, %(name)s, %(size)s, %(color)s, %(quantity)s,"
                " %(unit_price_cents)s, %(category)s)",
                {**line, "order_id": order_id},
            )
        await conn.execute(
            "UPDATE orders SET subtotal_cents = %s, shipping_cents = %s, total_cents = %s, saga_step = %s, updated_at = now()"
            " WHERE order_id = %s",
            (totals["subtotal_cents"], totals["shipping_cents"], totals["total_cents"], next_step, order_id),
        )


async def save_payment(pool: AsyncConnectionPool, order_id: str, payment_id: str, next_step: str) -> None:
    """Charge outcome (the payment id) and the next step in one write."""
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE orders SET payment_id = %s, saga_step = %s, updated_at = now() WHERE order_id = %s",
            (payment_id, next_step, order_id),
        )


async def settle(pool: AsyncConnectionPool, order_id: str, status: str, reason: str = "", shortages: list | None = None) -> bool:
    """PENDING -> CONFIRMED or FAILED. False if someone else already settled the order."""
    message = CONFIRMED_MESSAGE if status == "CONFIRMED" else reason
    async with pool.connection() as conn:
        return await change_status(conn, order_id, ("PENDING",), status, message, reason=reason, shortages=shortages or [])


async def load_order(pool: AsyncConnectionPool, order_id: str, user_id: str | None = None, history: bool = True) -> dict | None:
    """An order with its lines (and history). With `user_id`, someone else's order is simply not found."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT * FROM orders WHERE order_id = %s AND (%s::text IS NULL OR user_id = %s)", (order_id, user_id, user_id)
        )
        order = await cur.fetchone()
        if order is None:
            return None
        await _attach(conn, [order], history)
        return order


async def list_orders(pool: AsyncConnectionPool, user_id: str, limit: int, offset: int) -> tuple[list[dict], bool]:
    """A user's orders, newest first (lines but no history), and whether more follow."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT * FROM orders WHERE user_id = %s ORDER BY created_at DESC, order_id DESC LIMIT %s OFFSET %s",
            (user_id, limit + 1, offset),
        )
        orders = await cur.fetchall()
        page = orders[:limit]
        await _attach(conn, page, history=False)
        return page, len(orders) > limit


async def _attach(conn: AsyncConnection, orders: list[dict], history: bool) -> None:
    ids = [o["order_id"] for o in orders]
    cur = await conn.execute("SELECT * FROM order_lines WHERE order_id = ANY(%s) ORDER BY sku_id", (ids,))
    by_order: dict[str, list] = {i: [] for i in ids}
    for line in await cur.fetchall():
        by_order[line["order_id"]].append(line)
    events: dict[str, list] = {i: [] for i in ids}
    if history:
        cur = await conn.execute("SELECT * FROM order_events WHERE order_id = ANY(%s) ORDER BY event_id", (ids,))
        for e in await cur.fetchall():
            events[e["order_id"]].append(e)
    for o in orders:
        o["lines"], o["history"] = by_order[o["order_id"]], events[o["order_id"]]


async def unsettled_older_than(pool: AsyncConnectionPool, seconds: float) -> list[dict]:
    """Orders whose saga or cancellation may have died: PENDING, or claimed for cancelling, and untouched for `seconds`."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT * FROM orders WHERE (status = 'PENDING' OR saga_step = 'CANCELLING')"
            " AND updated_at <= now() - make_interval(secs => %s) ORDER BY updated_at",
            (seconds,),
        )
        orders = await cur.fetchall()
        await _attach(conn, orders, history=False)
        return orders


async def events_after(pool: AsyncConnectionPool, user_id: str, order_id: str, after_event_id: int) -> list[dict]:
    """The caller's outbox events with id > after (all their orders, or one), oldest first."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT * FROM order_events WHERE user_id = %s AND event_id > %s AND (%s = '' OR order_id = %s) ORDER BY event_id",
            (user_id, after_event_id, order_id, order_id),
        )
        return await cur.fetchall()


async def events_by_id(pool: AsyncConnectionPool, ids: list[int]) -> list[dict]:
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT * FROM order_events WHERE event_id = ANY(%s) ORDER BY event_id", (ids,))
        return await cur.fetchall()


async def events_since(pool: AsyncConnectionPool, after_event_id: int) -> list[dict]:
    """Every event after an id (the listener's catch-up after its connection dropped)."""
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT * FROM order_events WHERE event_id > %s ORDER BY event_id", (after_event_id,))
        return await cur.fetchall()


async def latest_event_id(pool: AsyncConnectionPool) -> int:
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT COALESCE(max(event_id), 0) AS n FROM order_events")
        return (await cur.fetchone())["n"]
