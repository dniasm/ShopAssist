"""gRPC clients: one channel per service, a deadline on every unary call, errors translated to ApiError.

The orchestrator reaches data only through these. Routes look the stub method up at call time
(`clients.catalog.ListProducts`), which keeps the call sites short and lets tests observe real calls.
"""
import time

import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc

from orchestrator.errors import from_grpc
from orchestrator.settings import Settings
from shopassist_proto.svcauth import aio_channel, load_key
from shopassist_proto import shopassist_pb2_grpc as pb_grpc

USER_HEADER = "x-user-id"

# name -> (fully-qualified gRPC service, stub class, Settings attribute holding its address)
SERVICES = {
    "catalog": ("shopassist.v2.CatalogService", pb_grpc.CatalogServiceStub, "catalog_addr"),
    "inventory": ("shopassist.v2.InventoryService", pb_grpc.InventoryServiceStub, "inventory_addr"),
    "order": ("shopassist.v2.OrderService", pb_grpc.OrderServiceStub, "order_addr"),
    "customer": ("shopassist.v2.CustomerService", pb_grpc.CustomerServiceStub, "customer_addr"),
    "payment": ("shopassist.v2.PaymentService", pb_grpc.PaymentServiceStub, "payment_addr"),
    "assistant": ("shopassist.v2.AssistantService", pb_grpc.AssistantServiceStub, "assistant_addr"),
}


class Clients:
    def __init__(self, settings: Settings):
        self._timeout = settings.unary_timeout
        self._health_timeout = settings.health_timeout
        self.addresses = {name: getattr(settings, attr) for name, (_, _, attr) in SERVICES.items()}
        key = load_key("orchestrator")  # raises when SVC_KEY_ORCHESTRATOR is missing: no unsigned calls
        self._channels = {name: aio_channel(addr, "orchestrator", audience=name, key=key) for name, addr in self.addresses.items()}
        self.catalog = SERVICES["catalog"][1](self._channels["catalog"])
        self.inventory = SERVICES["inventory"][1](self._channels["inventory"])
        self.order = SERVICES["order"][1](self._channels["order"])
        self.customer = SERVICES["customer"][1](self._channels["customer"])
        self.assistant = SERVICES["assistant"][1](self._channels["assistant"])

    async def call(self, service: str, rpc, request, user_id: str | None = None, source: str | None = None):
        """Run one unary RPC with the standard deadline; `user_id` is forwarded as `x-user-id` metadata and
        `source` (e.g. "assistant") as `x-source`, which the customer service puts on its cart audit lines."""
        metadata = tuple(m for m in ((USER_HEADER, user_id), ("x-source", source)) if m[1]) or None
        try:
            return await rpc(request, timeout=self._timeout, metadata=metadata)
        except grpc.aio.AioRpcError as e:
            raise from_grpc(e, service) from e

    async def probe(self, name: str) -> dict:
        """Ask one service's standard grpc.health.v1 endpoint; down means no SERVING answer within the deadline."""
        grpc_name = SERVICES[name][0]
        stub = health_pb2_grpc.HealthStub(self._channels[name])
        started = time.perf_counter()
        error = None
        try:
            reply = await stub.Check(health_pb2.HealthCheckRequest(service=grpc_name), timeout=self._health_timeout)
            if reply.status != health_pb2.HealthCheckResponse.SERVING:
                error = f"reports {health_pb2.HealthCheckResponse.ServingStatus.Name(reply.status)}"
        except grpc.aio.AioRpcError as e:
            error = "no response (timed out)" if e.code() == grpc.StatusCode.DEADLINE_EXCEEDED else (e.details() or e.code().name)
        return {
            "name": name, "service": grpc_name, "address": self.addresses[name],
            "status": "down" if error else "up", "latency_ms": round((time.perf_counter() - started) * 1000),
            "error": error,
        }

    async def close(self) -> None:
        for channel in self._channels.values():
            await channel.close()

