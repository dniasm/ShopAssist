"""Wrong-password throttling, from day7's accounts (backend/library/auth.py).

After MAX_FAILURES wrong passwords for one email from one address within WINDOW seconds, that email waits there;
after MAX_ADDRESS_FAILURES from one address (any emails), the whole address waits. An email is locked only for the
address that keeps guessing it, so knowing someone's email is not enough to lock them out. Wrong invite codes count
against the address. In memory, like the event log: the orchestrator is a single process.
"""
import threading
import time
from collections.abc import Callable

MAX_FAILURES, WINDOW = 5, 15 * 60
MAX_ADDRESS_FAILURES = 20


class LoginLimits:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> list[float]:
        recent = [t for t in self._failures.get(key, []) if now - t < WINDOW]
        if recent:
            self._failures[key] = recent
        else:
            self._failures.pop(key, None)  # forget quiet keys so the map cannot grow without bound
        return recent

    def blocked(self, email: str, address: str) -> bool:
        now = self._clock()
        with self._lock:
            return (len(self._recent(f"{email.lower()}@{address}", now)) >= MAX_FAILURES
                    or len(self._recent(f"@{address}", now)) >= MAX_ADDRESS_FAILURES)

    def address_blocked(self, address: str) -> bool:
        with self._lock:
            return len(self._recent(f"@{address}", self._clock())) >= MAX_ADDRESS_FAILURES

    def failed(self, email: str, address: str) -> None:
        now = self._clock()
        with self._lock:
            if email:  # a wrong invite code has no email: it counts for the address only
                self._failures.setdefault(f"{email.lower()}@{address}", []).append(now)
            self._failures.setdefault(f"@{address}", []).append(now)
