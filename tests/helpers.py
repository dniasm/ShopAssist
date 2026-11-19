"""Helpers shared by the order/saga and API tests."""
import asyncio

import grpc
import psycopg
import pytest

from shopassist_proto import shopassist_pb2 as pb
from shopassist_proto import shopassist_pb2_grpc as pb_grpc
from tests.fixtures import INVENTORY, PRODUCTS, SKUS

PRICES = {p["product_id"]: p["price_cents"] for p in PRODUCTS}
SKU_PRICE = {s["sku_id"]: PRICES[s["product_id"]] for s in SKUS}


def sku_with(on_hand: int, skip: int = 0) -> str:
    """The id of a seeded SKU holding exactly `on_hand` units (`skip` picks a different one)."""
    return [i["sku_id"] for i in INVENTORY if i["on_hand"] == on_hand][skip]


def cheapest_sku_with(min_on_hand: int) -> str:
    """The seeded SKU with at least that much stock whose price is lowest (for shipping-threshold tests)."""
    candidates = [i["sku_id"] for i in INVENTORY if i["on_hand"] >= min_on_hand]
    return min(candidates, key=lambda s: SKU_PRICE[s])


def rows(url: str, sql: str, params=()):
    with psycopg.connect(url) as conn:
        return conn.execute(sql, params).fetchall()


def code_of(fn):
    with pytest.raises(grpc.RpcError) as e:
        fn()
    return e.value.code()


def as_user(user_id: str):
    return (("x-user-id", user_id),)


def checkout_request(key: str, lines, method: str = "card_ok") -> pb.CheckoutRequest:
    return pb.CheckoutRequest(
        idempotency_key=key, payment_method=method, lines=[pb.CheckoutLine(sku_id=s, quantity=q) for s, q in lines]
    )


async def checkout(addr: str, user: str, key: str, lines, method: str = "card_ok") -> list[pb.CheckoutEvent]:
    """Run one Checkout to the end of its stream; raises grpc.aio.AioRpcError if the stream breaks."""
    async with grpc.aio.insecure_channel(addr) as ch:
        call = pb_grpc.OrderServiceStub(ch).Checkout(checkout_request(key, lines, method), metadata=as_user(user))
        return [event async for event in call]


async def checkout_many(addr: str, specs: list[tuple]) -> list:
    """Run many checkouts at once; each result is its event list or the exception it raised."""
    return await asyncio.gather(*(checkout(addr, *spec) for spec in specs), return_exceptions=True)


def steps(events) -> list[tuple[str, str]]:
    return [(pb.CheckoutStep.Name(e.step), pb.StepState.Name(e.state).removeprefix("STEP_")) for e in events]


def get_order(addr: str, user: str, order_id: str) -> pb.Order:
    with grpc.insecure_channel(addr) as ch:
        return pb_grpc.OrderServiceStub(ch).GetOrder(pb.GetOrderRequest(order_id=order_id), metadata=as_user(user))


async def collect_events(
    addr: str, user: str, *, after: int = -1, order_id: str = "", stop=None, timeout: float = 15, keep_cursor: bool = False,
) -> list[pb.OrderEvent]:
    """Watch an order stream until `stop(events)` is true (or the timeout passes) and return what arrived.

    The cursor message that opens an `after=-1` stream (no order id) is dropped unless `keep_cursor`."""
    events: list[pb.OrderEvent] = []
    async with grpc.aio.insecure_channel(addr) as ch:
        call = pb_grpc.OrderServiceStub(ch).WatchOrders(
            pb.WatchOrdersRequest(order_id=order_id, after_event_id=after), metadata=as_user(user))

        async def read():
            async for event in call:
                if not event.order_id and not keep_cursor:
                    continue
                events.append(event)
                if stop is not None and stop(events):
                    return

        try:
            await asyncio.wait_for(read(), timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            call.cancel()
    return events


def advance(stack, step_seconds: float = 0) -> int:
    """Run one fulfilment pass on the order service's own loop and pool; returns how many orders moved a step."""
    from services.order import fulfilment
    return stack.loop_thread.call(fulfilment.advance_due(stack.order.service.pool, step_seconds))


def order_statuses(url: str, order_id: str) -> list[str]:
    return [r[0] for r in rows(url, "SELECT status FROM order_events WHERE order_id = %s ORDER BY event_id", (order_id,))]


FULL_JOURNEY = ["PENDING", "CONFIRMED", "PACKED", "SHIPPED", "OUT_FOR_DELIVERY", "DELIVERED"]


async def subscribed(stack, count: int, timeout: float = 15) -> None:
    """Wait until `count` WatchOrders streams are registered on the order service (a watcher only sees what happens after that)."""
    deadline = asyncio.get_running_loop().time() + timeout
    while stack.order.service.watch_subscribers() < count:
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"only {stack.order.service.watch_subscribers()} of {count} watchers subscribed")
        await asyncio.sleep(0.02)
