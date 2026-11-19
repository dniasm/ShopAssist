"""Live order events: ONE dedicated LISTEN connection per process, fanned out to every WatchOrders stream.

Why not one LISTEN per stream: a watcher costs a Postgres connection otherwise, and the pool must stay free for
real work. The NOTIFY payload is only an event id; the row is read from the outbox, so a lost or reordered
notification never changes what a watcher sees, only when.

Why subscribe BEFORE replaying: a stream registers here first, then reads the history, then drains its queue
(skipping ids the replay already delivered). Whatever commits in between reaches it through one of the two.
"""
import asyncio
import logging
from collections import deque

import psycopg
from psycopg_pool import AsyncConnectionPool

from services.order import db

log = logging.getLogger(__name__)
CHANNEL = "order_events"
RECONNECT_SECONDS = 0.5
RECENT = 2000  # ids remembered to drop an event that arrives both by NOTIFY and by catch-up


class WatchHub:
    def __init__(self, pool: AsyncConnectionPool):
        self.pool = pool
        self.ready = asyncio.Event()  # set while a LISTEN connection is established
        self._subs: dict[asyncio.Queue, tuple[str, str]] = {}
        self._recent: deque[int] = deque(maxlen=RECENT)
        self._recent_set: set[int] = set()
        self._last_id = 0

    # ---- for watchers ----

    def subscribe(self, user_id: str, order_id: str = "") -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subs[queue] = (user_id, order_id)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subs.pop(queue, None)

    def shutdown(self) -> None:
        """Tell every open stream to finish (a None wakes its handler), so stopping the server never waits on them."""
        for queue in list(self._subs):
            queue.put_nowait(None)

    def subscriber_count(self) -> int:
        return len(self._subs)

    # ---- the listener ----

    def publish(self, events: list[dict]) -> None:
        for event in events:
            event_id = event["event_id"]
            self._last_id = max(self._last_id, event_id)
            if event_id in self._recent_set:
                continue
            if len(self._recent) == self._recent.maxlen:
                self._recent_set.discard(self._recent[0])
            self._recent.append(event_id)
            self._recent_set.add(event_id)
            for queue, (user_id, order_id) in list(self._subs.items()):
                if event["user_id"] == user_id and (not order_id or event["order_id"] == order_id):
                    queue.put_nowait(event)

    async def run(self) -> None:
        """Listen forever. If Postgres drops the connection, reconnect and catch up from the last id seen."""
        first = True
        while True:
            try:
                async with await psycopg.AsyncConnection.connect(self.pool.conninfo, autocommit=True) as conn:
                    await conn.execute(f"LISTEN {CHANNEL}")  # LISTEN first, THEN read: nothing can slip between
                    if first:
                        self._last_id = await db.latest_event_id(self.pool)
                        first = False
                    else:
                        self.publish(await db.events_since(self.pool, self._last_id))  # what happened while we were away
                    self.ready.set()
                    async for notification in conn.notifies():
                        self.publish(await db.events_by_id(self.pool, [int(notification.payload)]))
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("order event listener lost its connection; reconnecting", exc_info=True)
            self.ready.clear()
            await asyncio.sleep(RECONNECT_SECONDS)
