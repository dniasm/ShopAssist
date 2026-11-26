"""Settle orders whose checkout saga or cancellation died (process crash, lost dependency) half-way.

Policy: roll FORWARD when the customer has paid and the stock commit still works; otherwise undo everything
(release the hold, refund) and mark the order FAILED with the reason "recovered after interruption".
"""
import asyncio
import contextlib
import logging

from services.order import db
from services.order.cancel import Canceller
from services.order.runs import Runs
from services.order.saga import Deps, InjectedCrash, Interrupted, Saga
from shared_common.common.log import get_request_id, request_id_scope

log = logging.getLogger(__name__)


class Recovery:
    def __init__(self, deps: Deps, runs: Runs, canceller: Canceller, min_age_seconds: float, interval_seconds: float):
        self.deps, self.runs, self.canceller = deps, runs, canceller
        self.min_age, self.interval = min_age_seconds, interval_seconds
        self._one_at_a_time = asyncio.Lock()

    async def recover_now(self) -> int:
        """One pass over PENDING (or half-cancelled) orders untouched for `min_age`; returns how many were settled.

        Orders with a live saga in this process are skipped. A dependency that is down just means the order
        stays PENDING until the next pass.
        """
        settled = 0
        caller_id = get_request_id()
        async with self._one_at_a_time:
            for order in await db.unsettled_older_than(self.deps.pool, self.min_age):
                if self.runs.is_live(order["order_id"]):
                    continue
                # a caller-set id (operator, test) is kept; otherwise each order is its own trace, ended with the order
                with contextlib.nullcontext() if caller_id != "-" else request_id_scope():
                    settled += await self._recover_one(order)
        return settled

    async def _recover_one(self, order: dict) -> int:
        log.info("recovering order %s (step %s)", order["order_id"], order["saga_step"])
        try:
            if order["saga_step"] == "CANCELLING":
                await self.canceller.finish(order, recovering=True)
            else:
                await Saga(self.deps, order, recovering=True).resume()
        except Interrupted as e:
            log.warning("recovery of %s postponed: %s", order["order_id"], e)
            return 0
        log.info("recovered order %s (was at step %s)", order["order_id"], order["saga_step"])
        return 1

    async def loop(self) -> None:
        """At startup and then every `interval` seconds; a failed pass is logged and retried."""
        while True:
            try:
                await self.recover_now()
            except Exception:
                log.exception("recovery pass failed")
            await asyncio.sleep(self.interval)
