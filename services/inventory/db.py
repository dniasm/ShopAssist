"""Inventory SQL. Every public function is one transaction; the stock rules live here, not in server.py.

Concurrency rules (why the code looks the way it does):
- Stock rows are only ever changed with an atomic conditional UPDATE (`... WHERE on_hand - reserved >= q`);
  never read-then-write, so two callers cannot both take the last unit.
- Every transaction touches stock rows in sku_id order, so two multi-line reservations can't deadlock.
- A transaction never holds stock rows of two reservations at once (the sweeper handles one per transaction).
"""
from dataclasses import dataclass, field

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from services.common.idempotency import check_replay, fingerprint


class ReservationNotFound(Exception):
    pass


class UnknownSku(Exception):
    pass


class WrongState(Exception):
    """The reservation is RELEASED/EXPIRED, so the requested operation cannot apply."""


@dataclass
class Shortage:
    sku_id: str
    requested: int
    available: int


@dataclass
class ReserveResult:
    ok: bool
    expires_at: object = None
    shortages: list[Shortage] = field(default_factory=list)


async def load_seed(conn: AsyncConnection, rows: list[dict]) -> int:
    async with conn.cursor() as cur:
        await cur.executemany(
            "INSERT INTO stock (sku_id, product_id, on_hand) VALUES (%(sku_id)s, %(product_id)s, %(on_hand)s)", rows
        )
    return len(rows)


async def get_stock(pool: AsyncConnectionPool, sku_ids: list[str], product_ids: list[str]) -> list[dict]:
    """Stock rows for the given SKUs and/or products (union). Nothing asked means nothing returned."""
    if not sku_ids and not product_ids:
        return []
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT sku_id, product_id, on_hand, reserved, on_hand - reserved AS available FROM stock"
            " WHERE sku_id = ANY(%s) OR product_id = ANY(%s) ORDER BY sku_id",
            (sku_ids, product_ids),
        )
        return await cur.fetchall()


def _lines_fingerprint(lines: dict[str, int]) -> str:
    return fingerprint(sorted(lines.items()))


async def _lock_reservation(conn: AsyncConnection, reservation_id: str) -> dict | None:
    cur = await conn.execute(
        "SELECT state, expires_at, fingerprint, expires_at <= now() AS past_due FROM reservations"
        " WHERE reservation_id = %s FOR UPDATE",
        (reservation_id,),
    )
    return await cur.fetchone()


async def _give_back(conn: AsyncConnection, reservation_id: str, new_state: str, reason: str = "") -> None:
    """Return a HELD reservation's units to available and record why. Caller holds the reservation row lock."""
    cur = await conn.execute(
        "SELECT sku_id, quantity FROM reservation_lines WHERE reservation_id = %s ORDER BY sku_id", (reservation_id,)
    )
    for line in await cur.fetchall():
        await conn.execute(
            "UPDATE stock SET reserved = reserved - %s WHERE sku_id = %s", (line["quantity"], line["sku_id"])
        )
    await conn.execute(
        "UPDATE reservations SET state = %s, reason = %s, updated_at = now() WHERE reservation_id = %s",
        (new_state, reason, reservation_id),
    )


async def reserve(pool: AsyncConnectionPool, reservation_id: str, lines: dict[str, int], ttl_seconds: int) -> ReserveResult:
    """Hold every line or none. A repeat of a live reservation id returns the stored result without holding again;
    a repeat with different lines raises KeyReused."""
    fp = _lines_fingerprint(lines)
    async with pool.connection() as conn:
        cur = await conn.execute(
            "INSERT INTO reservations (reservation_id, state, expires_at, fingerprint)"
            " VALUES (%s, 'HELD', now() + make_interval(secs => %s), %s) ON CONFLICT DO NOTHING RETURNING expires_at",
            (reservation_id, ttl_seconds, fp),
        )
        created = await cur.fetchone()
        if created is None:  # idempotent replay (the INSERT waited for any in-flight twin to finish)
            existing = await _lock_reservation(conn, reservation_id)
            check_replay(reservation_id, existing["fingerprint"], fp)  # before the state check: a different request is never "already released"
            if existing["state"] in ("RELEASED", "EXPIRED"):
                raise WrongState(f"reservation {reservation_id} is {existing['state']}")
            return ReserveResult(ok=True, expires_at=existing["expires_at"])
        shortages: list[Shortage] = []
        for sku_id, quantity in sorted(lines.items()):
            cur = await conn.execute(
                "UPDATE stock SET reserved = reserved + %s WHERE sku_id = %s AND on_hand - reserved >= %s",
                (quantity, sku_id, quantity),
            )
            if cur.rowcount == 0:
                shortages.append(Shortage(sku_id, quantity, await _available(conn, sku_id)))
            else:
                await conn.execute(
                    "INSERT INTO reservation_lines (reservation_id, sku_id, quantity) VALUES (%s, %s, %s)",
                    (reservation_id, sku_id, quantity),
                )
        if shortages:
            await conn.rollback()  # undoes the lines that did fit and the reservation row itself
            return ReserveResult(ok=False, shortages=shortages)
        return ReserveResult(ok=True, expires_at=created["expires_at"])


async def _available(conn: AsyncConnection, sku_id: str) -> int:
    cur = await conn.execute("SELECT on_hand - reserved AS available FROM stock WHERE sku_id = %s", (sku_id,))
    row = await cur.fetchone()
    return row["available"] if row else 0


async def commit(pool: AsyncConnectionPool, reservation_id: str) -> None:
    """HELD -> COMMITTED: the units leave on_hand. Repeats are no-ops; a lapsed hold is expired, not committed."""
    lapsed = False
    async with pool.connection() as conn:
        row = await _lock_reservation(conn, reservation_id)
        if row is None:
            raise ReservationNotFound(reservation_id)
        if row["state"] == "COMMITTED":
            return
        if row["state"] != "HELD":
            raise WrongState(f"reservation {reservation_id} is {row['state']}")
        if row["past_due"]:
            await _give_back(conn, reservation_id, "EXPIRED", "expired before commit")
            lapsed = True
        else:
            cur = await conn.execute(
                "SELECT sku_id, quantity FROM reservation_lines WHERE reservation_id = %s ORDER BY sku_id", (reservation_id,)
            )
            for line in await cur.fetchall():
                await conn.execute(
                    "UPDATE stock SET on_hand = on_hand - %s, reserved = reserved - %s WHERE sku_id = %s",
                    (line["quantity"], line["quantity"], line["sku_id"]),
                )
            await conn.execute(
                "UPDATE reservations SET state = 'COMMITTED', updated_at = now() WHERE reservation_id = %s", (reservation_id,)
            )
    if lapsed:  # raised after the transaction closed so the expiry above is kept
        raise WrongState(f"reservation {reservation_id} expired before it was committed")


async def release(pool: AsyncConnectionPool, reservation_id: str, reason: str) -> None:
    """HELD -> RELEASED. Unknown, already released or expired ids are no-ops (compensation may run twice)."""
    async with pool.connection() as conn:
        row = await _lock_reservation(conn, reservation_id)
        if row is None or row["state"] in ("RELEASED", "EXPIRED"):
            return
        if row["state"] == "COMMITTED":
            raise WrongState(f"reservation {reservation_id} is already committed; restock instead")
        await _give_back(conn, reservation_id, "RELEASED", reason)


async def restock(pool: AsyncConnectionPool, idempotency_key: str, lines: dict[str, int]) -> None:
    """Add units back to on_hand once per key. An unknown SKU aborts everything, including the key;
    reusing a key with different lines raises KeyReused."""
    fp = _lines_fingerprint(lines)
    async with pool.connection() as conn:
        cur = await conn.execute(
            "INSERT INTO restocks (idempotency_key, fingerprint) VALUES (%s, %s) ON CONFLICT DO NOTHING RETURNING 1",
            (idempotency_key, fp),
        )
        if await cur.fetchone() is None:
            cur = await conn.execute("SELECT fingerprint FROM restocks WHERE idempotency_key = %s", (idempotency_key,))
            check_replay(idempotency_key, (await cur.fetchone())["fingerprint"], fp)
            return
        for sku_id, quantity in sorted(lines.items()):
            cur = await conn.execute("UPDATE stock SET on_hand = on_hand + %s WHERE sku_id = %s", (quantity, sku_id))
            if cur.rowcount == 0:
                raise UnknownSku(sku_id)  # the exception rolls the whole transaction back


async def expire_due(pool: AsyncConnectionPool, batch: int = 100) -> int:
    """Expire HELD reservations past expires_at, one transaction each; returns how many this call expired."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT reservation_id FROM reservations WHERE state = 'HELD' AND expires_at <= now()"
            " ORDER BY expires_at LIMIT %s",
            (batch,),
        )
        due = [r["reservation_id"] for r in await cur.fetchall()]
    expired = 0
    for reservation_id in due:
        async with pool.connection() as conn:
            cur = await conn.execute(
                "SELECT 1 FROM reservations WHERE reservation_id = %s AND state = 'HELD' AND expires_at <= now()"
                " FOR UPDATE SKIP LOCKED",
                (reservation_id,),
            )
            if await cur.fetchone():  # still due and not being handled by a commit/release right now
                await _give_back(conn, reservation_id, "EXPIRED", "ttl elapsed")
                expired += 1
    return expired
