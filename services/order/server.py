"""OrderService (:50053): orders, the checkout saga, recovery. Run: python -m services.order.server"""
import asyncio
import logging
import os
from pathlib import Path

import grpc
from dotenv import load_dotenv

from services.common.auth import require_user
from services.common.db import create_pool, database_url, safe_target
from services.common.idempotency import KeyReused, fingerprint
from services.common.migrate import apply_migrations
from services.common.seed import read_seed, seed_dir as default_seed_dir, seed_if_empty
from services.common.server import Service, listen_address
from shopassist_proto.svcauth import aio_channel
from services.common.util import decode_offset, encode_offset, page_size
from services.order import convert, db
from services.order.cancel import Canceller, NotCancellable, OrderNotFound
from services.order.fulfilment import fulfilment_loop
from services.order.recovery import Recovery
from services.order.runs import RunBroken, Runs
from services.order.saga import Deps, InjectedCrash, Interrupted, Saga
from services.order.watch import WatchHub
from shopassist_proto import shopassist_pb2 as pb
from shopassist_proto import shopassist_pb2_grpc as pb_grpc

log = logging.getLogger("order")
MIGRATIONS = Path(__file__).parent / "migrations"
WAIT_FOR_RECOVERY_SECONDS = 120
DEFAULT_FULFILMENT_STEP_SECONDS = 8
NO_REPLAY = -1  # WatchOrders(after_event_id=-1): no replay; the first message is a cursor (order_id "", status UNSPECIFIED,
#                 event_id = the latest event id) proving the stream is registered, then only new events follow


def sku_categories(catalog: dict | None) -> dict[str, str]:
    """sku_id -> category from catalog.json, used to label historic order lines (empty if the seed is absent)."""
    if not catalog:
        return {}
    by_product = {p["product_id"]: p["category"] for p in catalog["products"]}
    return {s["sku_id"]: by_product[s["product_id"]] for s in catalog["skus"] if s["product_id"] in by_product}


def merge_lines(lines) -> dict[str, int]:
    if not lines:
        raise ValueError("the checkout has no lines")
    merged: dict[str, int] = {}
    for line in lines:
        if not line.sku_id or line.quantity <= 0:
            raise ValueError("every line needs a sku_id and a quantity > 0")
        merged[line.sku_id] = merged.get(line.sku_id, 0) + line.quantity
    return merged


class OrderServicer(pb_grpc.OrderServiceServicer):
    def __init__(self, deps: Deps, runs: Runs, canceller: Canceller, watch: WatchHub):
        self.deps, self.runs, self.canceller, self.watch = deps, runs, canceller, watch

    async def Checkout(self, request, context):
        user_id = await require_user(context)
        if not request.idempotency_key:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "idempotency_key is required")
        if request.lines:
            order_id, created, requested = await self._open(request, user_id, context)
        else:
            # A retry after success finds the cart already emptied: no lines means "replay my checkout".
            order_id, created, requested = await db.find_checkout(self.deps.pool, user_id, request.idempotency_key), False, {}
            if order_id is None:
                await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "the checkout has no lines, and this key has no earlier checkout")
        if created:
            order = await db.load_order(self.deps.pool, order_id, history=False)
            self.runs.start(order_id, lambda run: Saga(self.deps, order, run=run).execute(requested))
        try:
            async for event in self._follow(order_id):
                yield event
        except RunBroken:
            await context.abort(grpc.StatusCode.UNAVAILABLE, "checkout was interrupted; retry with the same idempotency key")
        except TimeoutError:
            await context.abort(grpc.StatusCode.UNAVAILABLE, "checkout is still being settled; retry with the same idempotency key")

    async def _open(self, request, user_id: str, context) -> tuple[str, bool, dict[str, int]]:
        """Validate a checkout with lines and create its order, or find the one this key already made."""
        try:
            if not request.payment_method:
                raise ValueError("payment_method is required")
            requested = merge_lines(request.lines)
        except ValueError as e:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(e))
        fp = fingerprint({"lines": sorted(requested.items()), "payment_method": request.payment_method})
        try:
            order_id, created = await db.begin_checkout(self.deps.pool, user_id, request.idempotency_key, fp, request.payment_method)
        except KeyReused as e:
            await context.abort(grpc.StatusCode.ALREADY_EXISTS, str(e))
        return order_id, created, requested

    async def _follow(self, order_id: str):
        """Live events if a saga is running here; otherwise the final event once the order is no longer PENDING."""
        deadline = asyncio.get_running_loop().time() + WAIT_FOR_RECOVERY_SECONDS
        while True:
            run = self.runs.get(order_id)
            if run is not None and run.live:
                async for event in run.stream():
                    yield event
                return
            order = await db.load_order(self.deps.pool, order_id)
            if order["status"] != "PENDING":
                yield convert.final_event(order)
                return
            if asyncio.get_running_loop().time() > deadline:
                raise TimeoutError(order_id)
            await asyncio.sleep(0.2)  # a dead saga: recovery will settle it

    async def GetOrder(self, request, context):
        user_id = await require_user(context)
        order = await db.load_order(self.deps.pool, request.order_id, user_id=user_id)
        if order is None:  # someone else's order looks exactly like a missing one
            await context.abort(grpc.StatusCode.NOT_FOUND, f"order {request.order_id} not found")
        return convert.order(order)

    async def CancelOrder(self, request, context):
        user_id = await require_user(context)
        if not request.order_id or not request.idempotency_key:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "order_id and idempotency_key are required")
        try:
            order = await self.canceller.cancel(user_id, request.order_id, request.idempotency_key)
        except OrderNotFound:
            await context.abort(grpc.StatusCode.NOT_FOUND, f"order {request.order_id} not found")
        except NotCancellable as e:
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, str(e))
        except (Interrupted, InjectedCrash) as e:  # the claim is saved, so recovery finishes it; a retry with the same key too
            await context.abort(grpc.StatusCode.UNAVAILABLE, f"cancellation was interrupted ({e}); retry with the same idempotency key")
        return convert.order(order)

    async def WatchOrders(self, request, context):
        """Replay the caller's events after `after_event_id`, then stream new ones. Never ends on its own."""
        user_id = await require_user(context)
        if request.order_id and await db.load_order(self.deps.pool, request.order_id, user_id=user_id, history=False) is None:
            await context.abort(grpc.StatusCode.NOT_FOUND, f"order {request.order_id} not found")
        try:
            await asyncio.wait_for(self.watch.ready.wait(), 5)  # LISTEN must be active before we replay, or events could slip by
        except TimeoutError:
            await context.abort(grpc.StatusCode.UNAVAILABLE, "the order event listener is not connected")
        inbox = self.watch.subscribe(user_id, request.order_id)  # subscribe, THEN replay, then drain: no gap
        try:
            replayed: set[int] = set()
            if request.after_event_id == NO_REPLAY:
                yield pb.OrderEvent(event_id=await db.latest_event_id(self.deps.pool))  # the cursor: "registered, you are up to date"
            else:
                for row in await db.events_after(self.deps.pool, user_id, request.order_id, request.after_event_id):
                    replayed.add(row["event_id"])
                    yield convert.order_event(row)
            while True:
                row = await inbox.get()
                if row is None:  # the service is shutting down
                    return
                if row["event_id"] in replayed:  # also arrived through the replay: already delivered
                    replayed.discard(row["event_id"])
                    continue
                yield convert.order_event(row)
        finally:
            self.watch.unsubscribe(inbox)

    async def ListOrders(self, request, context):
        user_id = await require_user(context)
        try:
            offset = decode_offset(request.page_token)
        except ValueError as e:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(e))
        limit = page_size(request.page_size)
        orders, more = await db.list_orders(self.deps.pool, user_id, limit, offset)
        return pb.ListOrdersResponse(
            orders=[convert.order(o) for o in orders], next_page_token=encode_offset(offset + len(orders)) if more else ""
        )


async def create_service(
    url: str, *, catalog_addr: str, inventory_addr: str, payment_addr: str, seed_dir: Path | None = None,
    address: str = "127.0.0.1:0", fault: str | None = None, reservation_ttl_seconds: int = 0,
    recovery_min_age_seconds: float = 60, recovery_interval_seconds: float = 30,
    fulfilment: bool = True, fulfilment_step_seconds: float | None = None, fulfilment_poll_seconds: float = 1.0,
) -> Service:
    pool = await create_pool(url)
    await apply_migrations(pool, MIGRATIONS)
    directory = seed_dir or default_seed_dir()
    orders = read_seed(directory, "orders.json")
    if orders is not None:
        payments = read_seed(directory, "payments.json") or []
        payment_ids = {p["order_id"]: p["payment_id"] for p in payments}
        categories = sku_categories(read_seed(directory, "catalog.json"))
        await seed_if_empty(pool, "orders", lambda conn: db.load_seed(conn, orders, payment_ids, categories))
        await db.backfill_categories(pool, categories)

    channels = {name: aio_channel(addr, "order", audience=name) for name, addr in
                (("catalog", catalog_addr), ("inventory", inventory_addr), ("payment", payment_addr))}
    deps = Deps(
        pool=pool, catalog=pb_grpc.CatalogServiceStub(channels["catalog"]),
        inventory=pb_grpc.InventoryServiceStub(channels["inventory"]), payment=pb_grpc.PaymentServiceStub(channels["payment"]),
        fault=fault, reservation_ttl=reservation_ttl_seconds,
    )
    runs = Runs()
    canceller = Canceller(deps)
    recovery = Recovery(deps, runs, canceller, recovery_min_age_seconds, recovery_interval_seconds)
    watch = WatchHub(pool)
    service = Service("order", pool)
    service.register(pb_grpc.add_OrderServiceServicer_to_server, OrderServicer(deps, runs, canceller, watch), "shopassist.v2.OrderService")
    service.run_in_background(watch.run)
    service.run_in_background(recovery.loop)
    if fulfilment:
        step = fulfilment_step_seconds if fulfilment_step_seconds is not None else float(
            os.environ.get("FULFILMENT_STEP_SECONDS") or DEFAULT_FULFILMENT_STEP_SECONDS)
        service.run_in_background(lambda: fulfilment_loop(pool, step, fulfilment_poll_seconds))
    service.watch_subscribers = watch.subscriber_count  # for tests and operators
    service.add_pre_stop(runs.cancel_all)  # sagas still running die with the server, like a crash; their streams then end

    async def end_watch_streams() -> None:  # open WatchOrders streams finish cleanly instead of being cancelled
        watch.shutdown()

    service.add_pre_stop(end_watch_streams)
    service.add_closer(lambda: asyncio.gather(*(c.close() for c in channels.values())))
    service.recover_now = recovery.recover_now  # for tests and operators
    service.bind(address)
    return service


async def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s order %(levelname)s %(message)s")
    url = database_url("ORDER_DATABASE_URL")
    log.info("database %s", safe_target(url))
    service = await create_service(
        url, address=listen_address("ORDER_ADDR", "localhost:50053"),
        catalog_addr=os.environ.get("CATALOG_ADDR") or "localhost:50051",
        inventory_addr=os.environ.get("INVENTORY_ADDR") or "localhost:50052",
        payment_addr=os.environ.get("PAYMENT_ADDR") or "localhost:50055",
        fault=os.environ.get("ORDER_FAULT") or None,
        reservation_ttl_seconds=int(os.environ.get("RESERVATION_TTL_SECONDS") or 0),
    )
    await service.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
    except KeyboardInterrupt:
        pass
