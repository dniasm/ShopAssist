"""Keeps ONE OrderService.WatchOrders stream open for every user who has a session WebSocket open.

Why per user and not per socket: three tabs of one user must not mean three streams (or three copies of every
event); the user's event log in `EventHub` already fans out to all their sockets.

The stream starts when the first socket opens and stops `stop_after` seconds after the last one closes, so a page
refresh does not churn it. The last event id seen is remembered per user, so a dropped stream (or a user coming
back later) resumes with `after_event_id` and neither misses nor repeats an event.
"""
import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field

import grpc
import grpc.aio

from orchestrator.checkout_flow import clear_temporal_facts
from orchestrator.clients import USER_HEADER, Clients
from orchestrator.events import EventHub
from shared_common.common.log import set_request_id
from shopassist_proto import shopassist_pb2 as pb

log = logging.getLogger(__name__)
FROM_NOW = -1  # WatchOrders: "no replay, only new events"
MAX_BACKOFF_SECONDS = 5.0
RESUME_OVERLAP = 100  # ids to re-read on resume: events can commit out of id order, so "after the last id" can lose one
REMEMBERED = 2000  # forwarded ids kept per user, to drop the repeats that the overlap re-reads


@dataclass
class _Watch:
    sockets: int = 0
    last_event_id: int = FROM_NOW
    floor: int = FROM_NOW  # the cursor of the first stream: events at or below it were already history when the user arrived
    forwarded: deque = field(default_factory=lambda: deque(maxlen=REMEMBERED))
    forwarded_set: set = field(default_factory=set)
    task: asyncio.Task | None = None
    ready: asyncio.Event = field(default_factory=asyncio.Event)  # set once the Order service says the stream is registered
    stop_timer: asyncio.TimerHandle | None = None
    stopping: bool = field(default=False)
    cleared: set = field(default_factory=set)  # orders whose temporal facts were already cleared


    def resume_after(self) -> int:
        """Where to resume: a little BEFORE the last forwarded id (never before the arrival cursor), because an event with
        a lower id can commit after a higher one; `forward` drops whatever was already sent."""
        if self.last_event_id == FROM_NOW:
            return FROM_NOW
        return max(self.floor, self.last_event_id - RESUME_OVERLAP)

    def first_time(self, event_id: int) -> bool:
        """True (and remembers it) if this event id has not been forwarded yet."""
        if event_id in self.forwarded_set:
            return False
        if len(self.forwarded) == self.forwarded.maxlen:
            self.forwarded_set.discard(self.forwarded[0])
        self.forwarded.append(event_id)
        self.forwarded_set.add(event_id)
        return True


class OrderWatchers:
    def __init__(self, clients: Clients, hub: EventHub, stop_after: float = 30.0, retry_after: float = 1.0):
        self._clients, self._hub = clients, hub
        self._stop_after, self._retry_after = stop_after, retry_after
        self._watches: dict[str, _Watch] = {}

    def socket_opened(self, user_id: str) -> None:
        watch = self._watches.setdefault(user_id, _Watch())
        watch.sockets += 1
        if watch.stop_timer is not None:
            watch.stop_timer.cancel()
            watch.stop_timer = None
        if watch.task is None or watch.task.done():
            watch.task = asyncio.create_task(self._run(user_id, watch), name=f"watch-orders-{user_id}")

    def socket_closed(self, user_id: str) -> None:
        watch = self._watches.get(user_id)
        if watch is None:
            return
        watch.sockets = max(0, watch.sockets - 1)
        if watch.sockets == 0 and watch.stop_timer is None:
            watch.stop_timer = asyncio.get_running_loop().call_later(self._stop_after, self._stop, watch)

    async def wait_ready(self, user_id: str, timeout: float = 1.5) -> None:
        """Wait until the user's order stream is registered with the Order service, so that nothing that happens after
        the client's `hello` can be missed. Gives up after `timeout` (an unreachable Order service must not block sockets)."""
        watch = self._watches.get(user_id)
        if watch is not None:
            try:
                await asyncio.wait_for(watch.ready.wait(), timeout)
            except asyncio.TimeoutError:
                log.warning("order stream for %s not ready after %.1fs; continuing", user_id, timeout)

    def active(self) -> int:
        """Streams currently running (for tests and the System page)."""
        return sum(1 for w in self._watches.values() if w.task is not None and not w.task.done())

    def _stop(self, watch: _Watch) -> None:
        watch.stop_timer = None
        if watch.sockets == 0 and watch.task is not None:
            watch.task.cancel()

    async def close(self) -> None:
        tasks = [w.task for w in self._watches.values() if w.task is not None]
        for w in self._watches.values():
            if w.stop_timer is not None:
                w.stop_timer.cancel()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _order_confirmed(self, user_id: str, watch: _Watch, order_id: str) -> None:
        """Once per order: a bought item settles the user's current need, however the order got confirmed."""
        if order_id in watch.cleared:
            return
        watch.cleared.add(order_id)
        try:
            order = await self._clients.call("order", self._clients.order.GetOrder, pb.GetOrderRequest(order_id=order_id), user_id=user_id)
            await clear_temporal_facts(self._clients, user_id, order)
        except Exception as e:  # noqa: BLE001 - housekeeping must never break the order stream
            log.warning("could not clear temporal facts of %s after order %s: %s", user_id, order_id, type(e).__name__)

    async def _run(self, user_id: str, watch: _Watch) -> None:
        """Stream, forward, and on any failure wait a moment and resume from the last event id."""
        set_request_id()  # the watch outlives the socket that opened it: its own trace
        log.info("order watch started user=%s", user_id)
        delay = self._retry_after
        while True:
            call = self._clients.order.WatchOrders(
                pb.WatchOrdersRequest(after_event_id=watch.resume_after()), metadata=((USER_HEADER, user_id),)
            )
            try:
                async for event in call:
                    delay = self._retry_after
                    if not event.order_id:  # the opening cursor of a from-now stream: registered, and this is where we are
                        watch.last_event_id = max(watch.last_event_id, event.event_id)
                        if watch.floor == FROM_NOW:
                            watch.floor = event.event_id
                        watch.ready.set()
                        continue
                    watch.last_event_id = max(watch.last_event_id, event.event_id)
                    if not watch.first_time(event.event_id):
                        continue  # re-read because of the resume overlap; the client already has it
                    if event.status == pb.CONFIRMED:
                        await self._order_confirmed(user_id, watch, event.order_id)
                    self._hub.publish(user_id, "order", "status", {
                        "order_id": event.order_id, "status": pb.OrderStatus.Name(event.status),
                        "message": event.message, "event_id": event.event_id,
                    })
            except grpc.aio.AioRpcError as e:
                log.warning("order watch for %s dropped (%s); resuming near event %s", user_id, e.code().name, watch.last_event_id)
            finally:
                call.cancel()
                watch.ready.set()  # whatever happened, nobody should wait on a stream that is not coming
            await asyncio.sleep(delay)
            delay = min(delay * 2, MAX_BACKOFF_SECONDS)
