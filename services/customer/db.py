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
