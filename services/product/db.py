"""Product service storage (owns products.db)."""
import re

from services.common import connection, prepare_db

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    product_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    color TEXT NOT NULL,
    price REAL NOT NULL,
    description TEXT NOT NULL
);
"""
DEFAULT_LIMIT = 10


def _load_seed(conn, records) -> None:
    conn.executemany(
        "INSERT INTO products (product_id, name, category, color, price, description) VALUES (?,?,?,?,?,?)",
        [(p["product_id"], p["name"], p["category"], p["color"], p["price"], p["description"]) for p in records],
    )


def init_db(db_path, seed_path) -> None:
    prepare_db(db_path, SCHEMA, "SELECT COUNT(*) FROM products", seed_path, _load_seed)


def get_product(db_path, product_id: str):
    with connection(db_path) as conn:
        return conn.execute("SELECT * FROM products WHERE product_id = ?", (product_id,)).fetchone()


def search_products(db_path, text: str, category: str, max_price: float, limit: int):
    """Every word of `text` must appear in the name, description, category or color (case-insensitive)."""
    where, params = [], []
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        where.append("lower(name || ' ' || description || ' ' || category || ' ' || color) LIKE ?")
        params.append(f"%{word}%")
    if category:
        where.append("lower(category) = ?")
        params.append(category.lower())
    if max_price > 0:
        where.append("price <= ?")
        params.append(max_price)
    sql = "SELECT * FROM products"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY price, product_id LIMIT ?"
    params.append(limit if limit > 0 else DEFAULT_LIMIT)
    with connection(db_path) as conn:
        return conn.execute(sql, params).fetchall()
