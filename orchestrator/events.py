"""Per-user event log behind the session WebSocket.

Why a log and not just a broadcast: a browser tab that reconnects (or opens late) must not miss or repeat
events, so every event gets a per-user sequence number and the last N are kept for replay. If the client's
cursor is older than what we kept (or newer than anything we have, e.g. after a restart) we say `resync`
and the client refetches its state instead of trusting a gappy stream.
"""
import asyncio
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class _UserLog:
    events: deque
    seq: int = 0
    subscribers: list[asyncio.Queue] = field(default_factory=list)
    touched: float = 0.0  # last publish or last socket close, for expiry


class EventHub:
    def __init__(self, max_events: int = 500, keep_seconds: float = 3600, clock: Callable[[], float] = time.monotonic):
        self.max_events, self.keep_seconds, self._clock = max_events, keep_seconds, clock
        self._users: dict[str, _UserLog] = {}

    def _log(self, user_id: str) -> _UserLog:
        if user_id not in self._users:
            self._users[user_id] = _UserLog(events=deque(maxlen=self.max_events), touched=self._clock())
        return self._users[user_id]

    def latest_seq(self, user_id: str) -> int:
        log = self._users.get(user_id)
        return log.seq if log else 0

    def publish(self, user_id: str, channel: str, type_: str, data: dict) -> dict:
        """Append an event for the user and hand it to every open socket. Returns the envelope."""
        self.purge()
        log = self._log(user_id)
        log.seq += 1
        log.touched = self._clock()
        envelope = {"seq": log.seq, "channel": channel, "type": type_, "data": data,
                    "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds")}
        log.events.append(envelope)
        for queue in log.subscribers:
            queue.put_nowait(envelope)
        return envelope

    def subscribe(self, user_id: str) -> asyncio.Queue:
        """A queue of events published from now on. Call `replay` right after, with no await in between, for a gapless start."""
        self.purge()
        queue: asyncio.Queue = asyncio.Queue()
        self._log(user_id).subscribers.append(queue)
        return queue

    def unsubscribe(self, user_id: str, queue: asyncio.Queue) -> None:
        log = self._users.get(user_id)
        if log and queue in log.subscribers:
            log.subscribers.remove(queue)
            log.touched = self._clock()

    def replay(self, user_id: str, after: int | None) -> tuple[list[dict], bool]:
        """(events with seq > after, resync). `after=None` means the client wants nothing replayed."""
        if after is None:
            return [], False
        log = self._users.get(user_id)
        latest = log.seq if log else 0
        oldest_kept = log.events[0]["seq"] if log and log.events else latest + 1
        if after > latest or after < oldest_kept - 1:
            return [], True
        return [e for e in (log.events if log else ()) if e["seq"] > after], False

    def purge(self) -> None:
        """Forget users with no open socket whose last activity is older than `keep_seconds`."""
        now = self._clock()
        for user_id in [u for u, log in self._users.items() if not log.subscribers and now - log.touched > self.keep_seconds]:
            del self._users[user_id]
