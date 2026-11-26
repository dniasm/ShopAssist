"""The checkout saga: validate -> reserve stock -> charge -> commit stock, undoing earlier steps on failure.

WRITE-AHEAD RULE: `orders.saga_step` is saved BEFORE each remote call and updated with the outcome right after.
If this process dies at any point, recovery reads the recorded step and finishes or undoes the saga.

Recovery may re-issue a write only when it WANTS that write to happen (Commit and Release of this order's hold,
Refund of a payment we know succeeded: all idempotent). It must never re-issue the charge: if the crash came
before the request reached Payment, that would bill the customer for the first time minutes after they were told
the checkout was interrupted. For the charge it only LOOKS (PaymentService.LookupCharge, read-only).
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import grpc
import grpc.aio

from services.common.util import dollars, iso
from services.order import convert, db
from shared_common.common.log import audit
from shopassist_proto import shopassist_pb2 as pb

log = logging.getLogger(__name__)

SHIPPING_CENTS = 499
FREE_SHIPPING_FROM_CENTS = 5000
RECOVERED = "recovered after interruption"
CODES = grpc.StatusCode


class InjectedCrash(Exception):
    """Raised by the ORDER_FAULT test hook to simulate the process dying at that point."""


class Interrupted(Exception):
    """A remote call failed in a way that leaves its outcome unknown. The order stays PENDING for recovery."""


@dataclass
class Deps:
    pool: object
    catalog: object                   # generated gRPC stubs, typed loosely on purpose
    inventory: object
    payment: object
    fault: str | None = None          # test hook: "after_reserve" | "before_charge" | "after_charge" (see _crash_point)
    reservation_ttl: int = 0          # 0 = the inventory default
    call_timeout: float = 5.0


async def remote_call(rpc, request, timeout: float, tolerate: tuple = ()):
    """One remote call. Codes in `tolerate` come back as the error; any other failure leaves the outcome unknown."""
    try:
        return await rpc(request, timeout=timeout), None
    except grpc.aio.AioRpcError as e:
        if e.code() in tolerate:
            return None, e
        raise Interrupted(f"{e.code().name}: {e.details()}") from e


def crash_point(deps: "Deps", name: str, recovering: bool = False) -> None:
    """Test hook (ORDER_FAULT): simulate the process dying right here. Recovery never crashes on purpose."""
    if deps.fault == name and not recovering:
        raise InjectedCrash(name)


def shipping_for(subtotal_cents: int) -> int:
    return 0 if subtotal_cents >= FREE_SHIPPING_FROM_CENTS else SHIPPING_CENTS


class Saga:
    def __init__(self, deps: Deps, order: dict, run=None, recovering: bool = False):
        self.deps, self.run, self.recovering = deps, run, recovering
        self.order_id, self.user_id = order["order_id"], order["user_id"]
        self.method, self.total = order["payment_method"], order["total_cents"]
        self.payment_id, self.step = order["payment_id"], order["saga_step"]
        self.lines: list[dict] = order.get("lines", [])

    # ---------------- entry points ----------------

    async def execute(self, requested: dict[str, int]) -> None:
        """A live checkout from the first step."""
        if await self._validate(requested) and await self._reserve() and await self._charge():
            await self._confirm()

    async def resume(self) -> None:
        """Finish or undo a saga that died, from the step recorded on the order."""
        if self.step in ("VALIDATING", "RESERVING"):
            await self._compensate()  # nothing was charged yet; Release is a no-op if the hold never happened
        elif self.step == "CHARGING":
            if await self._learn_charge():  # LOOK at the charge, never make it
                await self._confirm()
        elif self.step == "CONFIRMING":
            await self._confirm()
        elif self.step == "COMPENSATING":
            await self._compensate()

    # ---------------- steps ----------------

    async def _validate(self, requested: dict[str, int]) -> bool:
        await self._emit(pb.VALIDATING, pb.STEP_STARTED, "Checking items and prices")
        reply, err = await self._call(
            self.deps.catalog.GetSkus, pb.GetSkusRequest(sku_ids=sorted(requested)), tolerate=(CODES.UNAVAILABLE, CODES.DEADLINE_EXCEEDED)
        )
        if err:
            await self._emit(pb.VALIDATING, pb.STEP_FAILED, "The catalog is not reachable right now")
            return await self._fail("the catalog is unavailable; please try again")
        found = {i.sku.sku_id: i for i in reply.items}
        missing = sorted(set(requested) - set(found))
        if missing:
            await self._emit(pb.VALIDATING, pb.STEP_FAILED, f"Unknown item: {', '.join(missing)}")
            return await self._fail(f"unknown SKU: {', '.join(missing)}")
        self.lines = [
            {"sku_id": s, "product_id": found[s].sku.product_id, "name": found[s].name, "size": found[s].sku.size,
             "color": found[s].sku.color, "quantity": requested[s], "unit_price_cents": found[s].price_cents,
             "category": found[s].category}
            for s in sorted(requested)
        ]
        subtotal = sum(l["quantity"] * l["unit_price_cents"] for l in self.lines)
        shipping = shipping_for(subtotal)
        self.total = subtotal + shipping
        totals = {"subtotal_cents": subtotal, "shipping_cents": shipping, "total_cents": self.total}
        await db.save_priced_lines(self.deps.pool, self.order_id, self.lines, totals, next_step="RESERVING")  # write-ahead
        audit("checkout_started", user=self.user_id, order=self.order_id, lines=len(self.lines), total=dollars(self.total))
        await self._emit(pb.VALIDATING, pb.STEP_SUCCEEDED, f"{len(self.lines)} item(s), total {self.total} cents")
        return True

    async def _reserve(self) -> bool:
        await self._emit(pb.RESERVING, pb.STEP_STARTED, "Holding your items")
        request = pb.ReserveRequest(
            reservation_id=self.order_id, ttl_seconds=self.deps.reservation_ttl,
            lines=[pb.StockLine(sku_id=l["sku_id"], quantity=l["quantity"]) for l in self.lines],
        )
        reply, _ = await self._call(self.deps.inventory.Reserve, request)
        self._crash_point("after_reserve")
        if not reply.ok:
            await self._emit(pb.RESERVING, pb.STEP_FAILED, "Not enough stock", shortages=reply.shortages)
            shortages = [{"sku_id": s.sku_id, "requested": s.requested, "available": s.available} for s in reply.shortages]
            return await self._fail("not enough stock", shortages)  # Reserve is all-or-nothing: nothing to undo
        await db.set_step(self.deps.pool, self.order_id, "CHARGING")  # outcome saved, and write-ahead for the charge
        await self._emit(pb.RESERVING, pb.STEP_SUCCEEDED, "Items held")
        return True

    async def _charge(self) -> bool:
        await self._emit(pb.CHARGING, pb.STEP_STARTED, "Charging your card")
        self._crash_point("before_charge")  # the write-ahead CHARGING step is saved; the request has not left yet
        request = pb.ChargeRequest(
            idempotency_key=f"charge:{self.order_id}", order_id=self.order_id, amount_cents=self.total, payment_method=self.method
        )
        reply, err = await self._call(self.deps.payment.Charge, request, tolerate=(CODES.INVALID_ARGUMENT,))
        self._crash_point("after_charge")
        if err is not None or reply.status != pb.PAYMENT_SUCCEEDED:
            reason = err.details() if err is not None else (reply.decline_reason or "declined")
            await self._emit(pb.CHARGING, pb.STEP_FAILED, reason)
            await self._compensate(f"payment declined: {reason}")
            return False
        self.payment_id = reply.payment_id
        await db.save_payment(self.deps.pool, self.order_id, reply.payment_id, "CONFIRMING")  # outcome + write-ahead for commit
        audit("order_placed", user=self.user_id, order=self.order_id)
        await self._emit(pb.CHARGING, pb.STEP_SUCCEEDED, "Payment received")
        return True

    async def _learn_charge(self) -> bool:
        """Recovery at CHARGING: what became of the charge? True = it succeeded and the payment id is saved (roll forward).

        SUCCEEDED -> roll forward. DECLINED, or the key never reached Payment (NOT_FOUND) -> undo; no money moved.
        """
        reply, err = await self._call(
            self.deps.payment.LookupCharge, pb.LookupChargeRequest(idempotency_key=f"charge:{self.order_id}"),
            tolerate=(CODES.NOT_FOUND,),
        )
        if err is not None or reply.status != pb.PAYMENT_SUCCEEDED:
            await self._compensate()
            return False
        self.payment_id = reply.payment_id
        await db.save_payment(self.deps.pool, self.order_id, reply.payment_id, "CONFIRMING")
        audit("order_placed", user=self.user_id, order=self.order_id)
        return True

    async def _confirm(self) -> None:
        await self._emit(pb.CONFIRMING, pb.STEP_STARTED, "Confirming your order")
        _, err = await self._call(
            self.deps.inventory.Commit, pb.CommitRequest(reservation_id=self.order_id),
            tolerate=(CODES.FAILED_PRECONDITION, CODES.NOT_FOUND),
        )
        if err is not None:  # the hold lapsed while the customer was paying: give the money back
            await self._emit(pb.CONFIRMING, pb.STEP_FAILED, "The stock hold expired before payment completed")
            await self._compensate("reservation expired before payment completed")
            return
        if await db.settle(self.deps.pool, self.order_id, "CONFIRMED"):
            audit("order_confirmed", user=self.user_id, order=self.order_id)
        await self._emit(pb.CONFIRMING, pb.STEP_SUCCEEDED, "Stock committed")
        await self._finish(pb.STEP_SUCCEEDED, "Order confirmed.")

    async def _compensate(self, reason: str = RECOVERED) -> None:
        """Undo whatever happened: release the hold, refund a successful charge, mark the order FAILED."""
        reason = RECOVERED if self.recovering else reason
        await db.set_step(self.deps.pool, self.order_id, "COMPENSATING")
        await self._emit(pb.COMPENSATING, pb.STEP_STARTED, "Undoing earlier steps")
        await self._call(self.deps.inventory.Release, pb.ReleaseRequest(reservation_id=self.order_id, reason=reason))
        if self.payment_id:
            await self._call(self.deps.payment.Refund, pb.RefundRequest(
                idempotency_key=f"refund:{self.order_id}", payment_id=self.payment_id, amount_cents=self.total))
        if await db.settle(self.deps.pool, self.order_id, "FAILED", reason):
            audit("order_failed", user=self.user_id, order=self.order_id, reason=reason)
        await self._emit(pb.COMPENSATING, pb.STEP_SUCCEEDED, "Everything undone")
        await self._finish(pb.STEP_FAILED, reason)

    async def _fail(self, reason: str, shortages: list[dict] | None = None) -> bool:
        """End a checkout that failed before anything needed undoing. Always returns False (for `and` chains)."""
        if await db.settle(self.deps.pool, self.order_id, "FAILED", reason, shortages):
            audit("order_failed", user=self.user_id, order=self.order_id, reason=reason)
        await self._finish(pb.STEP_FAILED, reason)
        return False

    # ---------------- plumbing ----------------

    async def _finish(self, state: int, message: str) -> None:
        if self.run is not None:
            order = await db.load_order(self.deps.pool, self.order_id)
            final = convert.final_event(order)
            final.state, final.message = state, message
            await self.run.emit(final)

    async def _emit(self, step: int, state: int, message: str, shortages=()) -> None:
        if self.run is not None:
            await self.run.emit(pb.CheckoutEvent(
                order_id=self.order_id, step=step, state=state, message=message, shortages=shortages,
                timestamp=iso(datetime.now(timezone.utc)),
            ))

    async def _call(self, rpc, request, tolerate: tuple = ()):
        return await remote_call(rpc, request, self.deps.call_timeout, tolerate)

    def _crash_point(self, name: str) -> None:
        crash_point(self.deps, name, self.recovering)
