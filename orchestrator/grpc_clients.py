"""Async gRPC clients for the three services. The orchestrator talks to data only through these."""
import asyncio
import time
from collections.abc import AsyncIterator

import grpc
import grpc.aio

from shopassist_proto import shopassist_pb2 as pb
from shopassist_proto import shopassist_pb2_grpc as pb_grpc

UNARY_TIMEOUT = 5.0  # seconds
HEALTH_TIMEOUT = 1.5  # seconds: a health probe must answer quickly or count as down
HEALTH_PROBE_ID = "__health__"  # an id no service holds: NOT_FOUND proves the service is up and answering


class ServiceError(Exception):
    """A gRPC call failed. str(e) is safe to show to the user."""


class NotFound(ServiceError):
    pass


class ServiceUnavailable(ServiceError):
    pass


def _translate(err: grpc.aio.AioRpcError, service: str, addr: str) -> ServiceError:
    code = err.code()
    if code == grpc.StatusCode.NOT_FOUND:
        return NotFound(err.details() or "not found")
    if code in (grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED):
        return ServiceUnavailable(f"The {service} service is not reachable at {addr}.")
    return ServiceError(f"The {service} service failed: {err.details() or code.name}")


class Clients:
    def __init__(self, product_addr: str, inventory_addr: str, order_addr: str):
        self._addrs = {"product": product_addr, "inventory": inventory_addr, "order": order_addr}
        self._channels = [grpc.aio.insecure_channel(a) for a in self._addrs.values()]
        self._product = pb_grpc.ProductServiceStub(self._channels[0])
        self._inventory = pb_grpc.InventoryServiceStub(self._channels[1])
        self._order = pb_grpc.OrderServiceStub(self._channels[2])

    async def probe(self, name: str) -> dict:
        """Check one service over gRPC with a short deadline. No database is touched from here.

        Calls an RPC on an id that cannot exist. NOT_FOUND (Product, Order) or an empty answer (Inventory) means the
        service is up; UNAVAILABLE, a timeout or any other failure means down.
        """
        service, method = {"product": ("ProductService", "GetProduct"), "inventory": ("InventoryService", "GetStock"),
                           "order": ("OrderService", "GetOrder")}[name]
        calls = {
            "product": lambda: self._product.GetProduct(pb.GetProductRequest(product_id=HEALTH_PROBE_ID), timeout=HEALTH_TIMEOUT),
            "inventory": lambda: self._inventory.GetStock(pb.StockRequest(product_id=HEALTH_PROBE_ID), timeout=HEALTH_TIMEOUT),
            "order": lambda: self._order.GetOrder(pb.GetOrderRequest(order_id=HEALTH_PROBE_ID), timeout=HEALTH_TIMEOUT),
        }
        started = time.perf_counter()
        error = None
        try:
            await calls[name]()
        except grpc.aio.AioRpcError as err:
            if err.code() != grpc.StatusCode.NOT_FOUND:
                error = err.code().name
        elapsed = round((time.perf_counter() - started) * 1000, 1)
        return {"name": name, "service": service, "method": method, "address": self._addrs[name],
                "status": "down" if error else "up", "latency_ms": None if error else elapsed, "error": error}

    async def health(self) -> list[dict]:
        return list(await asyncio.gather(*(self.probe(n) for n in ("product", "inventory", "order"))))

    async def close(self) -> None:
        for ch in self._channels:
            await ch.close()

    async def _unary(self, service: str, call, request):
        try:
            return await call(request, timeout=UNARY_TIMEOUT)
        except grpc.aio.AioRpcError as err:
            raise _translate(err, service, self._addrs[service]) from err

    async def search_products(self, text="", category="", max_price=0.0, limit=0) -> list[pb.Product]:
        req = pb.SearchProductsRequest(text=text, category=category, max_price=max_price, limit=limit)
        return list((await self._unary("product", self._product.SearchProducts, req)).products)

    async def get_product(self, product_id: str) -> pb.Product:
        return await self._unary("product", self._product.GetProduct, pb.GetProductRequest(product_id=product_id))

    async def get_stock(self, product_id: str, size: str = "") -> list[pb.StockLevel]:
        req = pb.StockRequest(product_id=product_id, size=size)
        return list((await self._unary("inventory", self._inventory.GetStock, req)).levels)

    async def get_order(self, order_id: str) -> pb.Order:
        return await self._unary("order", self._order.GetOrder, pb.GetOrderRequest(order_id=order_id))

    async def track_order(self, order_id: str) -> AsyncIterator[pb.OrderStatusUpdate]:
        """Yield each status update; the stream ends at DELIVERED/CANCELLED. No overall deadline (steps are slow)."""
        call = self._order.TrackOrder(pb.TrackOrderRequest(order_id=order_id))
        try:
            async for update in call:
                yield update
        except grpc.aio.AioRpcError as err:
            raise _translate(err, "order", self._addrs["order"]) from err
        finally:
            call.cancel()  # no-op once finished; stops the server stream if the consumer bails out early
