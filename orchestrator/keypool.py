"""API-key pool that spreads LLM calls across keys and respects Groq's four rate limits.

Groq reports two of the limits in response headers and not the other two:
  - x-ratelimit-*-requests  -> RPD (requests per DAY)      }  tracked from headers
  - x-ratelimit-*-tokens    -> TPM (tokens per MINUTE)     }
  - RPM (requests per minute) and TPD (tokens per day) are not in headers -> tracked locally here.
Reset headers are durations such as "2m59.56s", never timestamps.

Keys are never logged or returned; they are referred to as "key#<n>" (+ the last 4 characters at most).
"""
import asyncio
import logging
import re
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

RPM_WINDOW = 60.0
TPD_WINDOW = 86400.0
DEFAULT_COOLDOWN = 60.0  # when a 429 gives neither retry-after nor a usable reset header
MIN_COOLDOWN = 1.0       # a "retry-after: 0" must not make us hammer the same key

_DURATION_RE = re.compile(r"(?:\d+(?:\.\d+)?(?:ms|h|m|s))+")
_PART_RE = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")
_UNIT_SECONDS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}


def parse_duration(value: str | None) -> float | None:
    """Parse '2m59.56s', '7.66s', '1h2m3s', '250ms' (or a plain number of seconds) into seconds; None if invalid."""
    if value is None:
        return None
    value = value.strip()
    try:
        return float(value)
    except ValueError:
        pass
    if not _DURATION_RE.fullmatch(value):
        return None
    return sum(float(n) * _UNIT_SECONDS[u] for n, u in _PART_RE.findall(value))


def _int_header(headers: Mapping, name: str) -> int | None:
    try:
        return int(float(headers.get(name)))
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class Limits:
    rpm: int = 30
    rpd: int = 1000
    tpm: int = 8000
    tpd: int = 200000


@dataclass(frozen=True)
class Lease:
    index: int
    key: str = field(repr=False)  # the secret; excluded from repr so it cannot leak through logs or asserts


@dataclass
class _KeyState:
    index: int
    key: str = field(repr=False)
    label: str = ""
    disabled: bool = False
    cooling_until: float = 0.0
    rpm_calls: deque = field(default_factory=deque)   # start times of requests in the last 60 s
    tpd_events: deque = field(default_factory=deque)  # (time, total_tokens) in the last 24 h
    rpd_left: int | None = None                       # from x-ratelimit-remaining-requests (per day)
    rpd_reset_at: float = 0.0
    tpm_left: int | None = None                       # from x-ratelimit-remaining-tokens (per minute)
    tpm_reset_at: float = 0.0


class KeyPool:
    def __init__(self, keys: tuple[str, ...] | list[str], limits: Limits | None = None,
                 now: Callable[[], float] = time.monotonic):
        self._limits = limits or Limits()
        self._now = now  # injectable so tests can move time without sleeping
        self._lock = asyncio.Lock()  # guards selection and accounting; concurrent queries share the pool
        self._keys = [
            _KeyState(i, k, label=f"key#{i + 1}" + (f" …{k[-4:]}" if len(k) >= 12 else ""))
            for i, k in enumerate(keys)
        ]
        self._rr = 0

    def __len__(self) -> int:
        return len(self._keys)

    # ---- headroom (call with the lock held) ----

    def _left(self, st: _KeyState, t: float) -> dict[str, int]:
        lim = self._limits
        while st.rpm_calls and st.rpm_calls[0] <= t - RPM_WINDOW:
            st.rpm_calls.popleft()
        while st.tpd_events and st.tpd_events[0][0] <= t - TPD_WINDOW:
            st.tpd_events.popleft()
        # A header value only holds until its reset time; after that the window is back to the full limit.
        rpd = st.rpd_left if st.rpd_left is not None and st.rpd_reset_at > t else lim.rpd
        tpm = st.tpm_left if st.tpm_left is not None and st.tpm_reset_at > t else lim.tpm
        return {
            "rpm": lim.rpm - len(st.rpm_calls),
            "rpd": rpd,
            "tpm": tpm,
            "tpd": lim.tpd - sum(n for _, n in st.tpd_events),
        }

    def _state(self, st: _KeyState, t: float) -> str:
        if st.disabled:
            return "disabled"
        return "cooling" if st.cooling_until > t else "ok"

    def _score(self, st: _KeyState, t: float, est_tokens: int) -> float | None:
        """Lowest remaining fraction across the four limits, or None if the key cannot serve this request."""
        if self._state(st, t) != "ok":
            return None
        left = self._left(st, t)
        if left["rpm"] < 1 or left["rpd"] < 1 or left["tpm"] < est_tokens or left["tpd"] < est_tokens:
            return None
        lim = {"rpm": self._limits.rpm, "rpd": self._limits.rpd, "tpm": self._limits.tpm, "tpd": self._limits.tpd}
        return min(min(1.0, left[k] / lim[k]) if lim[k] > 0 else 0.0 for k in left)

    # ---- public API ----

    async def acquire(self, est_tokens: int, exclude: set[int] | frozenset[int] = frozenset()) -> Lease | None:
        """Pick the eligible key with the most headroom (ties: round-robin) and count this request against its RPM."""
        async with self._lock:
            t = self._now()
            scored = [(st, s) for st in self._keys if st.index not in exclude
                      if (s := self._score(st, t, est_tokens)) is not None]
            if not scored:
                return None
            top = max(s for _, s in scored)
            tied = [st for st, s in scored if s == top]
            chosen = next((st for st in tied if st.index >= self._rr), tied[0])
            self._rr = (chosen.index + 1) % len(self._keys)
            chosen.rpm_calls.append(t)
            return Lease(chosen.index, chosen.key)

    async def record(self, index: int, status: int, headers: Mapping, total_tokens: int | None = None) -> None:
        """Account for a response: refresh header-based limits, charge TPD, cool (429) or disable (401/403) the key."""
        async with self._lock:
            st = self._keys[index]
            t = self._now()
            self._read_headers(st, headers, t)
            if status in (401, 403):
                st.disabled = True
                log.warning("key#%d rejected (HTTP %d); disabled for this process", st.index + 1, status)
            elif status == 429:
                cooldown = self._cooldown(headers)
                st.cooling_until = t + cooldown
                log.warning("key#%d rate limited (HTTP 429); cooling for %.1fs", st.index + 1, cooldown)
            elif 200 <= status < 300 and total_tokens:
                st.tpd_events.append((t, total_tokens))

    async def status(self) -> list[dict]:
        async with self._lock:
            t = self._now()
            wall = datetime.now(timezone.utc)
            out = []
            for st in self._keys:
                left = self._left(st, t)
                cooling = st.cooling_until > t and not st.disabled
                out.append({
                    "key": st.label,
                    "state": self._state(st, t),
                    "rpm_left": left["rpm"], "rpd_left": left["rpd"], "tpm_left": left["tpm"], "tpd_left": left["tpd"],
                    "cooling_until": (wall + timedelta(seconds=st.cooling_until - t)).isoformat(timespec="seconds")
                    if cooling else None,
                })
            return out

    # ---- helpers ----

    @staticmethod
    def _read_headers(st: _KeyState, headers: Mapping, t: float) -> None:
        # *-requests is the DAILY window, *-tokens is the PER-MINUTE window.
        rem = _int_header(headers, "x-ratelimit-remaining-requests")
        if rem is not None:
            st.rpd_left = rem
            st.rpd_reset_at = t + (parse_duration(headers.get("x-ratelimit-reset-requests")) or TPD_WINDOW)
        rem = _int_header(headers, "x-ratelimit-remaining-tokens")
        if rem is not None:
            st.tpm_left = rem
            st.tpm_reset_at = t + (parse_duration(headers.get("x-ratelimit-reset-tokens")) or RPM_WINDOW)

    @staticmethod
    def _cooldown(headers: Mapping) -> float:
        retry_after = parse_duration(headers.get("retry-after"))
        if retry_after is None:
            # No retry-after: use the reset of whichever window is exhausted (daily requests / per-minute tokens).
            req_reset = parse_duration(headers.get("x-ratelimit-reset-requests"))
            tok_reset = parse_duration(headers.get("x-ratelimit-reset-tokens"))
            if _int_header(headers, "x-ratelimit-remaining-requests") == 0 and req_reset:
                retry_after = req_reset
            elif tok_reset:
                retry_after = tok_reset
            else:
                retry_after = req_reset or DEFAULT_COOLDOWN
        return max(retry_after, MIN_COOLDOWN)
