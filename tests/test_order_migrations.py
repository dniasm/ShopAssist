"""Upgrade tests: a migration must be safe for a database that already holds data, not only for a fresh one.

Pattern for every migration that changes existing tables: build the database as it was BEFORE the migration, put
realistic rows in it, apply the migration, then check the old rows are untouched and behave as before.
"""
import asyncio

import psycopg

from services.common.db import create_pool
from services.common.migrate import apply_migrations
from services.order import db, fulfilment
from services.order.server import MIGRATIONS
from tests.conftest import run

# Rows as the service wrote them before migration 003 (no `tracked`, no `cancel_key` columns yet).
OLD_ORDERS = [
    ("O-10022", "U-01", "CONFIRMED"), ("O-10023", "U-01", "PACKED"), ("O-10024", "U-02", "SHIPPED"),
    ("O-10025", "U-02", "OUT_FOR_DELIVERY"), ("O-10026", "U-03", "DELIVERED"), ("O-10027", "U-03", "CANCELLED"),
]


def test_migration_003_leaves_existing_orders_alone_and_the_worker_never_touches_them(temp_dbs):
    url = temp_dbs.create("orders")

    async def scenario():
        pool = await create_pool(url)
        try:
            await apply_migrations(pool, MIGRATIONS, max_version=2)  # the database as the shared stack had it
            async with pool.connection() as conn:
                for order_id, user_id, status in OLD_ORDERS:
                    await conn.execute(
                        "INSERT INTO orders (order_id, user_id, status, saga_step, created_at, updated_at)"
                        " VALUES (%s, %s, %s, 'DONE', '2026-04-01T10:00:00Z', '2026-04-01T10:00:00Z')", (order_id, user_id, status))
                    await conn.execute(
                        "INSERT INTO order_events (order_id, user_id, status, message) VALUES (%s, %s, %s, 'seeded')", (order_id, user_id, status))
            assert await apply_migrations(pool, MIGRATIONS) == ["003_tracking.sql"]  # the migration under test
            moved = await fulfilment.advance_due(pool, 0)  # their timestamps are ancient: they would be "due" if tracked
            # a checkout made after the upgrade IS tracked and does advance
            order_id, created = await db.begin_checkout(pool, "U-04", "after-upgrade", "fp", "card_ok")
            async with pool.connection() as conn:
                await db.change_status(conn, order_id, ("PENDING",), "CONFIRMED", "confirmed")
            moved_after = await fulfilment.advance_due(pool, 0)
            return moved, moved_after, order_id
        finally:
            await pool.close()

    moved, moved_after, new_order = run(scenario())
    with psycopg.connect(url) as conn:
        old = conn.execute("SELECT order_id, status, tracked FROM orders WHERE order_id = ANY(%s) ORDER BY order_id",
                           ([o[0] for o in OLD_ORDERS],)).fetchall()
        events = conn.execute("SELECT count(*) FROM order_events WHERE order_id = ANY(%s)", ([o[0] for o in OLD_ORDERS],)).fetchone()[0]
        new = conn.execute("SELECT tracked, status FROM orders WHERE order_id = %s", (new_order,)).fetchone()
    assert old == [(o, s, False) for o, _, s in OLD_ORDERS]  # same statuses as before, all untracked
    assert events == len(OLD_ORDERS) and moved == 0  # the worker wrote nothing for them
    assert new == (True, "PACKED") and moved_after == 1  # only the new order was moved
