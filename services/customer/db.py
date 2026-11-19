"""Customer SQL: seed loading and user lookups (cart SQL arrives in phase 5.2)."""
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool


async def load_seed(conn: AsyncConnection, data: dict) -> int:
    """Insert users from customers.json ({"users": [...]}); every user also gets an empty cart."""
    users = data["users"]
    async with conn.cursor() as cur:
        await cur.executemany(
            "INSERT INTO users (user_id, name, email, city) VALUES (%(user_id)s, %(name)s, %(email)s, %(city)s)",
            [{"city": "", **u} for u in users],
        )
        await cur.executemany("INSERT INTO carts (user_id) VALUES (%(user_id)s)", users)
    return len(users)


async def list_users(pool: AsyncConnectionPool) -> list[dict]:
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT user_id, name, email, city FROM users ORDER BY user_id")
        return await cur.fetchall()


async def get_user(pool: AsyncConnectionPool, user_id: str) -> dict | None:
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT user_id, name, email, city FROM users WHERE user_id = %s", (user_id,))
        return await cur.fetchone()


class UnknownUser(Exception):
    pass


class VersionMismatch(Exception):
    """ClearCart was given an if_version that is no longer the cart's version."""


async def _lock_cart(conn: AsyncConnection, user_id: str) -> dict:
    """The user's cart row, locked for the rest of the transaction (carts are created lazily for known users)."""
    cur = await conn.execute("SELECT 1 FROM users WHERE user_id = %s", (user_id,))
    if await cur.fetchone() is None:
        raise UnknownUser(user_id)
    await conn.execute("INSERT INTO carts (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (user_id,))
    cur = await conn.execute("SELECT version, updated_at FROM carts WHERE user_id = %s FOR UPDATE", (user_id,))
    return await cur.fetchone()


async def _bump(conn: AsyncConnection, user_id: str) -> None:
    await conn.execute("UPDATE carts SET version = version + 1, updated_at = now() WHERE user_id = %s", (user_id,))


async def _read_cart(conn: AsyncConnection, user_id: str) -> dict:
    cur = await conn.execute("SELECT user_id, version, updated_at FROM carts WHERE user_id = %s", (user_id,))
    cart = await cur.fetchone()
    cur = await conn.execute(
        "SELECT sku_id, product_id, quantity, added_at FROM cart_lines WHERE user_id = %s ORDER BY added_at, sku_id", (user_id,)
    )
    cart["lines"] = await cur.fetchall()
    return cart


async def get_cart(pool: AsyncConnectionPool, user_id: str) -> dict:
    async with pool.connection() as conn:
        await _lock_cart(conn, user_id)
        return await _read_cart(conn, user_id)


async def set_line(pool: AsyncConnectionPool, user_id: str, sku_id: str, product_id: str, quantity: int) -> dict:
    """Set one line's quantity (0 removes it). The version moves only when something actually changed."""
    async with pool.connection() as conn:
        await _lock_cart(conn, user_id)  # serialises edits of one cart, so the version counts every change
        cur = await conn.execute("SELECT quantity FROM cart_lines WHERE user_id = %s AND sku_id = %s", (user_id, sku_id))
        existing = await cur.fetchone()
        if quantity == 0:
            if existing:
                await conn.execute("DELETE FROM cart_lines WHERE user_id = %s AND sku_id = %s", (user_id, sku_id))
                await _bump(conn, user_id)
        elif existing is None:
            await conn.execute(
                "INSERT INTO cart_lines (user_id, sku_id, product_id, quantity) VALUES (%s, %s, %s, %s)",
                (user_id, sku_id, product_id, quantity),
            )
            await _bump(conn, user_id)
        elif existing["quantity"] != quantity:
            await conn.execute(
                "UPDATE cart_lines SET quantity = %s WHERE user_id = %s AND sku_id = %s", (quantity, user_id, sku_id)
            )
            await _bump(conn, user_id)
        return await _read_cart(conn, user_id)


async def clear_cart(pool: AsyncConnectionPool, user_id: str, if_version: int) -> dict:
    """Empty the cart; `if_version` (non-zero) makes it conditional on nobody having edited it since."""
    async with pool.connection() as conn:
        cart = await _lock_cart(conn, user_id)
        if if_version and cart["version"] != if_version:
            raise VersionMismatch(f"cart is at version {cart['version']}, not {if_version}")
        cur = await conn.execute("DELETE FROM cart_lines WHERE user_id = %s", (user_id,))
        if cur.rowcount:
            await _bump(conn, user_id)
        return await _read_cart(conn, user_id)
