"""Order service storage (owns orders.db)."""
from datetime import datetime, timezone

from services.common import connection, prepare_db
from shopassist_proto import shopassist_pb2 as pb

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    customer_name TEXT NOT NULL,
    status TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS order_items (
    order_id TEXT NOT NULL REFERENCES orders(order_id),
    product_id TEXT NOT NULL,
    size TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    unit_price REAL NOT NULL
);
"""

# The happy path a simulated order follows; CANCELLED is outside it.
FLOW = ["PLACED", "PACKED", "SHIPPED", "OUT_FOR_DELIVERY", "DELIVERED"]
TERMINAL = {"DELIVERED", "CANCELLED"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_seed(conn, records) -> None:
    for o in records:
        pb.OrderStatus.Value(o["status"])  # fail fast on a bad enum name
        conn.execute(
            "INSERT INTO orders (order_id, customer_name, status, updated_at) VALUES (?,?,?,?)",
            (str(o["order_id"]), o["customer_name"], o["status"], o["updated_at"]),
        )
        for it in o["items"]:
            conn.execute(
                "INSERT INTO order_items (order_id, product_id, size, quantity, unit_price) VALUES (?,?,?,?,?)",
                (str(o["order_id"]), it["product_id"], it["size"], it["quantity"], it["unit_price"]),
            )


def init_db(db_path, seed_path) -> None:
    prepare_db(db_path, SCHEMA, "SELECT COUNT(*) FROM orders", seed_path, _load_seed)


def get_order(db_path, order_id: str):
    """Return (order_row, item_rows) or None."""
    with connection(db_path) as conn:
        order = conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
        if order is None:
            return None
        items = conn.execute(
            "SELECT product_id, size, quantity, unit_price FROM order_items WHERE order_id = ? ORDER BY rowid",
            (order_id,),
        ).fetchall()
        return order, items


def advance_order(db_path, order_id: str):
    """Move an order one step along FLOW and persist it. Returns (status, updated_at) after the call.

    Terminal orders are left untouched. The read and write happen in one write
    transaction so two concurrent trackers cannot skip or repeat a step.
    """
    with connection(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT status, updated_at FROM orders WHERE order_id = ?", (order_id,)).fetchone()
        if row is None or row["status"] in TERMINAL:
            return (row["status"], row["updated_at"]) if row else None
        new_status = FLOW[FLOW.index(row["status"]) + 1]
        stamp = now_iso()
        conn.execute("UPDATE orders SET status = ?, updated_at = ? WHERE order_id = ?", (new_status, stamp, order_id))
        return new_status, stamp
