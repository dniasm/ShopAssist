"""Key pool: duration parsing, header-driven headroom, local RPM/TPD counters, cooling, disabling. No sleeping, no network."""
import asyncio
import json
import logging

import pytest

from orchestrator.keypool import KeyPool, Limits, parse_duration

K1, K2, K3 = "gsk_FAKEKEY_one_1234567890abcd", "gsk_FAKEKEY_two_1234567890wxyz", "gsk_FAKEKEY_three_123456789xyz9"
EST = 1300  # a typical estimated request size


class Clock:
    """Injectable clock: tests move time forward instead of sleeping."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def run(coro):
    return asyncio.run(coro)


def pool_of(keys=(K1, K2), clock=None, **limits) -> tuple[KeyPool, Clock]:
    clock = clock or Clock()
    return KeyPool(keys, Limits(**limits), now=clock), clock


async def pick(pool, est=EST, exclude=frozenset()):
    lease = await pool.acquire(est, exclude)
    return None if lease is None else lease.index


@pytest.mark.parametrize("text, seconds", [
    ("2m59.56s", 179.56),
    ("7.66s", 7.66),
    ("1h2m3s", 3723.0),
    ("250ms", 0.25),
    ("1m", 60.0),
    ("45", 45.0),          # retry-after is plain seconds
    ("0.5", 0.5),
    ("1m0.5s", 60.5),
])
def test_parse_duration(text, seconds):
    assert parse_duration(text) == pytest.approx(seconds)


@pytest.mark.parametrize("text", [None, "", "abc", "5x", "1m2", "m5s", "2m 3s"])
def test_parse_duration_invalid(text):
    assert parse_duration(text) is None


def test_headers_update_rpd_and_tpm_not_mixed_up():
    pool, clock = pool_of()

    async def go():
        await pool.record(0, 200, {
            "x-ratelimit-remaining-requests": "900", "x-ratelimit-reset-requests": "7.66s",   # RPD (per day)
            "x-ratelimit-remaining-tokens": "100", "x-ratelimit-reset-tokens": "2m59.56s",    # TPM (per minute)
        }, total_tokens=50)
        k1 = (await pool.status())[0]
        assert (k1["rpd_left"], k1["tpm_left"]) == (900, 100)
        assert k1["tpd_left"] == 200000 - 50  # TPD is local, fed by usage, not by headers
        # key 1 lacks token headroom for a 1300-token request, so the next call goes to key 2 (no 429 involved)
        assert await pick(pool) == 1
        assert await pick(pool, exclude={1}) is None          # key 1 alone is ineligible: 100 < 1300 tokens
        assert await pick(pool, est=50, exclude={1}) == 0   # a tiny request still fits on key 1

    run(go())


def test_header_headroom_recovers_after_reset_time():
    pool, clock = pool_of(keys=(K1,))

    async def go():
        await pool.record(0, 200, {"x-ratelimit-remaining-tokens": "100", "x-ratelimit-reset-tokens": "2m59.56s",
                                   "x-ratelimit-remaining-requests": "0", "x-ratelimit-reset-requests": "7.66s"}, 10)
        assert await pick(pool) is None  # RPD exhausted and TPM too low
        clock.advance(8)                 # RPD window resets (7.66s) but TPM (179.56s) has not
        assert await pick(pool) is None
        clock.advance(172)               # now past 179.56s
        assert await pick(pool) == 0
        k = (await pool.status())[0]
        assert (k["rpd_left"], k["tpm_left"]) == (1000 - 0, 8000)  # back to full limits

    run(go())


def test_local_rpm_counter_skips_key_then_recovers():
    pool, clock = pool_of(keys=(K1,), rpm=3)

    async def go():
        assert [await pick(pool) for _ in range(3)] == [0, 0, 0]
        assert await pick(pool) is None          # 3 calls inside 60 s: no RPM headroom
        assert (await pool.status())[0]["rpm_left"] == 0
        clock.advance(30)
        assert await pick(pool) is None          # still inside the window
        clock.advance(31)                        # 61 s after the first call
        assert await pick(pool) == 0
        assert (await pool.status())[0]["rpm_left"] == 2

    run(go())


def test_rpm_pushes_load_to_the_other_key():
    pool, _ = pool_of(rpm=2)

    async def go():
        got = [await pick(pool) for _ in range(5)]
        assert sorted(g for g in got if g is not None) == [0, 0, 1, 1]
        assert got[4] is None

    run(go())


def test_local_tpd_counter_fed_by_usage():
    pool, clock = pool_of(keys=(K1,), tpd=5000)

    async def go():
        await pool.record(0, 200, {}, total_tokens=4000)
        assert (await pool.status())[0]["tpd_left"] == 1000
        assert await pick(pool, est=EST) is None          # 1000 left < 1300 needed
        assert await pick(pool, est=900) == 0             # but a smaller request fits
        clock.advance(86400 + 1)                          # the 24 h window slides past the usage
        assert (await pool.status())[0]["tpd_left"] == 5000
        assert await pick(pool, est=EST) == 0

    run(go())


def test_429_with_retry_after_cools_key_and_routes_to_other():
    pool, clock = pool_of()

    async def go():
        assert await pick(pool) == 0
        await pool.record(0, 429, {"retry-after": "30"})
        st = (await pool.status())[0]
        assert st["state"] == "cooling" and st["cooling_until"] is not None
        assert [await pick(pool) for _ in range(3)] == [1, 1, 1]   # key 1 is never offered while cooling
        clock.advance(29)
        assert await pick(pool, exclude={1}) is None
        clock.advance(2)
        assert await pick(pool, exclude={1}) == 0
        assert (await pool.status())[0]["state"] == "ok"

    run(go())


def test_429_without_retry_after_uses_the_exhausted_window_reset():
    pool, clock = pool_of(keys=(K1,))

    async def go():
        await pool.record(0, 429, {"x-ratelimit-remaining-requests": "0", "x-ratelimit-reset-requests": "10m",
                                   "x-ratelimit-remaining-tokens": "5000", "x-ratelimit-reset-tokens": "2s"})
        clock.advance(120)
        assert (await pool.status())[0]["state"] == "cooling"  # waited for the daily reset (10 m), not the 2 s one
        clock.advance(481)
        assert await pick(pool) == 0

    run(go())


def test_429_cooldown_has_a_floor():
    pool, clock = pool_of(keys=(K1,))

    async def go():
        await pool.record(0, 429, {"retry-after": "0"})
        assert await pick(pool) is None  # does not allow an immediate hammer of the same key
        clock.advance(1.1)
        assert await pick(pool) == 0

    run(go())


@pytest.mark.parametrize("code", [401, 403])
def test_auth_failure_disables_key_permanently(code):
    pool, clock = pool_of()

    async def go():
        await pool.record(0, code, {})
        clock.advance(10 * 86400)
        assert [await pick(pool) for _ in range(3)] == [1, 1, 1]
        assert (await pool.status())[0]["state"] == "disabled"

    run(go())


def test_picks_most_headroom_then_round_robin():
    pool, _ = pool_of(keys=(K1, K2, K3))

    async def go():
        assert [await pick(pool) for _ in range(4)] == [0, 1, 2, 0]  # all fresh/tied: round-robin
        pool2, _ = pool_of(keys=(K1, K2))
        await pool2.record(0, 200, {"x-ratelimit-remaining-tokens": "4000", "x-ratelimit-reset-tokens": "30s"}, 10)
        assert await pick(pool2) == 1   # key 1 is at 50% token headroom, key 2 at 100%

    run(go())


def test_concurrent_acquires_never_oversubscribe():
    pool, _ = pool_of(keys=(K1,), rpm=4)

    async def go():
        return await asyncio.gather(*(pool.acquire(EST) for _ in range(10)))

    leases = run(go())
    assert sum(lease is not None for lease in leases) == 4


def test_no_full_key_in_status_repr_or_logs(caplog):
    caplog.set_level(logging.DEBUG)
    pool, _ = pool_of()

    async def go():
        lease = await pool.acquire(EST)
        assert K1 not in repr(lease) and lease.key == K1
        await pool.record(0, 429, {"retry-after": "5"})
        await pool.record(1, 401, {})
        return await pool.status()

    status = run(go())
    blob = json.dumps(status)
    for key in (K1, K2):
        assert key not in blob and key not in caplog.text
    assert status[0]["key"] == "key#1 …abcd" and status[1]["key"] == "key#2 …wxyz"
    assert "key#1" in caplog.text and "key#2" in caplog.text
    assert [s["state"] for s in status] == ["cooling", "disabled"]


def test_settings_repr_hides_keys():
    from orchestrator.settings import Settings
    s = Settings(llm_api_keys=("gsk_SECRETVALUE_1234",))
    assert "SECRETVALUE" not in repr(s)
