"""Assistant SQL: conversations, messages and facts. Every query is scoped by user id, so one user's id can never
reach another's rows (a conversation that is not yours is simply not found)."""
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from services.assistant import facts as rules

Clock = Callable[[], datetime]
CONVERSATION = "conversation_id, title, summary, created_at, updated_at"
MESSAGE = "message_id, conversation_id, role, text, cards::text AS cards_json, created_at"
FACT = "fact_id, kind, key, value, category, embedding, created_at, expires_at"


class NotFound(Exception):
    pass


# ---------------- conversations ----------------

async def create_conversation(pool: AsyncConnectionPool, user_id: str, title: str) -> dict:
    async with pool.connection() as conn:
        cur = await conn.execute(
            f"INSERT INTO conversations (conversation_id, user_id, title) VALUES (%s, %s, %s) RETURNING {CONVERSATION}",
            ("C-" + secrets.token_hex(6), user_id, title),
        )
        return await cur.fetchone()


async def list_conversations(pool: AsyncConnectionPool, user_id: str, limit: int) -> list[dict]:
    async with pool.connection() as conn:
        cur = await conn.execute(
            f"SELECT {CONVERSATION} FROM conversations WHERE user_id = %s ORDER BY updated_at DESC, created_at DESC, conversation_id LIMIT %s",
            (user_id, limit),
        )
        return await cur.fetchall()


async def get_conversation(pool: AsyncConnectionPool, user_id: str, conversation_id: str) -> dict:
    async with pool.connection() as conn:
        cur = await conn.execute(
            f"SELECT {CONVERSATION} FROM conversations WHERE conversation_id = %s AND user_id = %s", (conversation_id, user_id)
        )
        row = await cur.fetchone()
    if row is None:
        raise NotFound(conversation_id)
    return row


async def _update(pool: AsyncConnectionPool, user_id: str, conversation_id: str, column: str, value: str) -> dict:
    async with pool.connection() as conn:
        cur = await conn.execute(
            f"UPDATE conversations SET {column} = %s, updated_at = now() WHERE conversation_id = %s AND user_id = %s"
            f" RETURNING {CONVERSATION}",
            (value, conversation_id, user_id),
        )
        row = await cur.fetchone()
    if row is None:
        raise NotFound(conversation_id)
    return row


async def rename_conversation(pool: AsyncConnectionPool, user_id: str, conversation_id: str, title: str) -> dict:
    return await _update(pool, user_id, conversation_id, "title", title)


async def set_summary(pool: AsyncConnectionPool, user_id: str, conversation_id: str, summary: str) -> dict:
    return await _update(pool, user_id, conversation_id, "summary", summary)


async def delete_conversation(pool: AsyncConnectionPool, user_id: str, conversation_id: str) -> None:
    async with pool.connection() as conn:  # the messages go with it (ON DELETE CASCADE)
        cur = await conn.execute("DELETE FROM conversations WHERE conversation_id = %s AND user_id = %s", (conversation_id, user_id))
        if not cur.rowcount:
            raise NotFound(conversation_id)


# ---------------- messages ----------------

async def append_message(pool: AsyncConnectionPool, user_id: str, conversation_id: str, role: str, text: str, cards_json: str) -> dict:
    """Add a message and bump the conversation's updated_at, in one transaction."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "UPDATE conversations SET updated_at = now() WHERE conversation_id = %s AND user_id = %s", (conversation_id, user_id)
        )
        if not cur.rowcount:
            raise NotFound(conversation_id)
        cur = await conn.execute(
            f"INSERT INTO messages (conversation_id, role, text, cards) VALUES (%s, %s, %s, %s::jsonb) RETURNING {MESSAGE}",
            (conversation_id, role, text, cards_json or None),
        )
        return await cur.fetchone()


async def list_messages(pool: AsyncConnectionPool, user_id: str, conversation_id: str, limit: int) -> list[dict]:
    """Oldest first; with a limit, the latest `limit` messages (still oldest first). 0 means all of them."""
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT 1 FROM conversations WHERE conversation_id = %s AND user_id = %s", (conversation_id, user_id))
        if await cur.fetchone() is None:
            raise NotFound(conversation_id)
        cur = await conn.execute(
            f"SELECT * FROM (SELECT {MESSAGE} FROM messages WHERE conversation_id = %s ORDER BY message_id DESC"
            f" LIMIT nullif(%s, 0)) latest ORDER BY message_id",
            (conversation_id, limit),
        )
        return await cur.fetchall()


# ---------------- facts ----------------

async def _live_facts(conn: AsyncConnection, user_id: str, now: datetime) -> list[dict]:
    """The user's facts, after deleting the ones that have expired (expired facts are removed when read)."""
    await conn.execute("DELETE FROM facts WHERE user_id = %s AND expires_at IS NOT NULL AND expires_at <= %s", (user_id, now))
    cur = await conn.execute(f"SELECT {FACT} FROM facts WHERE user_id = %s ORDER BY created_at, fact_id", (user_id,))
    return await cur.fetchall()


async def list_facts(pool: AsyncConnectionPool, user_id: str, now: datetime) -> list[dict]:
    async with pool.connection() as conn:
        return await _live_facts(conn, user_id, now)


async def search_facts(pool: AsyncConnectionPool, user_id: str, text: str, embedding: list[float] | None,
                       limit: int, min_score: float, now: datetime) -> list[dict]:
    async with pool.connection() as conn:
        stored = await _live_facts(conn, user_id, now)
    return rules.rank(stored, text, embedding, limit, min_score, now)


async def apply_facts(pool: AsyncConnectionPool, user_id: str, changes: list[dict], now: datetime) -> list[dict]:
    """Plan and apply a batch of remember/forget changes atomically (one batch per user at a time); returns all live facts."""
    async with pool.connection() as conn:
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"facts:{user_id}",))
        inserts, deletes = rules.plan_changes(await _live_facts(conn, user_id, now), changes, now)
        if deletes:
            await conn.execute("DELETE FROM facts WHERE user_id = %s AND fact_id = ANY(%s)", (user_id, deletes))
        for i, row in enumerate(inserts):  # +i µs keeps a batch in the order given (listing is by created_at)
            await conn.execute(
                "INSERT INTO facts (fact_id, user_id, kind, key, value, category, embedding, created_at, expires_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s::real[], %s, %s)",
                (row["fact_id"], user_id, row["kind"], row["key"], row["value"], row["category"], row["embedding"], now + timedelta(microseconds=i), row["expires_at"]),
            )
        return await _live_facts(conn, user_id, now)


async def clear_temporal(pool: AsyncConnectionPool, user_id: str, categories: list[str]) -> int:
    """Remove the user's temporal facts in these categories; permanent ones stay. No category removes nothing."""
    if not categories:
        return 0
    async with pool.connection() as conn:
        cur = await conn.execute(
            "DELETE FROM facts WHERE user_id = %s AND kind = 'temporal' AND category = ANY(%s)",
            (user_id, categories),
        )
        return cur.rowcount
