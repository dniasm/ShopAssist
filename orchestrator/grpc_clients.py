"""Async gRPC clients for the three services. The orchestrator talks to data only through these."""
from collections.abc import AsyncIterator

import grpc
import grpc.aio

from shopassist_proto import shopassist_pb2 as pb
from shopassist_proto import shopassist_pb2_grpc as pb_grpc

UNARY_TIMEOUT = 5.0  # seconds


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
