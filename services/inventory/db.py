"""Inventory service storage (owns inventory.db)."""
from services.common import connection, prepare_db

SCHEMA = """
CREATE TABLE IF NOT EXISTS stock (
    product_id TEXT NOT NULL,
    size TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    PRIMARY KEY (product_id, size)
);
"""


def _load_seed(conn, records) -> None:
    conn.executemany(
        "INSERT INTO stock (product_id, size, quantity) VALUES (?,?,?)",
        [(r["product_id"], r["size"].upper(), r["quantity"]) for r in records],
    )


def init_db(db_path, seed_path) -> None:
    prepare_db(db_path, SCHEMA, "SELECT COUNT(*) FROM stock", seed_path, _load_seed)


def get_stock(db_path, product_id: str, size: str):
    sql = "SELECT product_id, size, quantity FROM stock WHERE product_id = ?"
    params = [product_id]
    if size:
        sql += " AND size = ?"
        params.append(size.upper())
    # Natural size order rather than alphabetical (L, M, S, XL).
    sql += " ORDER BY CASE size WHEN 'S' THEN 1 WHEN 'M' THEN 2 WHEN 'L' THEN 3 WHEN 'XL' THEN 4 ELSE 5 END, size"
    with connection(db_path) as conn:
        return conn.execute(sql, params).fetchall()
