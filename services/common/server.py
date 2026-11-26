"""grpc.aio server bootstrap: health service, background tasks, graceful shutdown.

A `Service` is built, started and stopped the same way in production (`serve_forever`) and in tests
(`start`/`stop` on a port-0 address), so tests exercise the real wiring.
"""
import asyncio
import contextlib
import logging
import os
import signal
import time
from collections.abc import Callable, Coroutine
from typing import Any

import grpc
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from psycopg_pool import AsyncConnectionPool

from services.common.auth import USER_HEADER
from shared_common.common.log import set_request_id, valid_request_id
from shopassist_proto.svcauth import CALLER_HEADER, HEALTH_CHECK_METHOD, REQUEST_ID_HEADER, AuthInterceptor

log = logging.getLogger(__name__)
STOP_PATIENCE_SECONDS = 5.0  # how long past the grace period we wait for gRPC to finish stopping


def listen_address(env_var: str, default: str) -> str:
    """Services listen on the same variable the orchestrator dials, e.g. CATALOG_ADDR=localhost:50051.

    In containers the address others dial ("catalog:50051") is not one to bind to, so GRPC_LISTEN_HOST
    (e.g. 0.0.0.0) replaces the host part and keeps the port.
    """
    address = os.environ.get(env_var) or default
    host = os.environ.get("GRPC_LISTEN_HOST")
    return f"{host}:{address.rsplit(':', 1)[1]}" if host else address


class RequestIdInterceptor(grpc.aio.ServerInterceptor):
    """Continue the caller's request id inside each call and log one access line per call (status, time, caller, user).

    The id is set inside the wrapped handler, which runs in the call's own task, so it cannot leak into another call.
    Calls the client streams to are passed through untouched (the proto has none).
    """

    async def intercept_service(self, continuation, handler_call_details):
        handler = await continuation(handler_call_details)
        if handler is None or handler.request_streaming:
            return handler
        md = {k: v for k, v in (handler_call_details.invocation_metadata or ()) if isinstance(v, str)}
        method = handler_call_details.method
        name = method.rsplit(".", 1)[-1]  # "CatalogService/ListProducts"
        ok_level = logging.DEBUG if method == HEALTH_CHECK_METHOD else logging.INFO
        who = f"caller={md.get(CALLER_HEADER, '-')} user={md.get(USER_HEADER, '-')}"
        incoming = md.get(REQUEST_ID_HEADER)

        def line(started: float, code: grpc.StatusCode) -> None:
            ms = int((time.perf_counter() - started) * 1000)
            log.log(ok_level if code == grpc.StatusCode.OK else logging.WARNING, "%s -> %s %dms %s", name, code.name, ms, who)

        def failed(started: float, context, error: BaseException) -> None:
            if isinstance(error, grpc.aio.AbortError):  # the servicer ended the call with a status on purpose
                line(started, context.code() or grpc.StatusCode.UNKNOWN)
            elif isinstance(error, asyncio.CancelledError):  # the client went away
                log.info("%s -> CANCELLED %dms %s", name, int((time.perf_counter() - started) * 1000), who)
            else:
                log.error("%s failed %s", name, who, exc_info=error)

        if handler.response_streaming:
            async def stream(request, context):
                set_request_id(valid_request_id(incoming))
                started = time.perf_counter()
                try:
                    async for item in handler.unary_stream(request, context):
                        yield item
                except (Exception, asyncio.CancelledError) as e:
                    failed(started, context, e)
                    raise
                line(started, context.code() or grpc.StatusCode.OK)

            return grpc.unary_stream_rpc_method_handler(stream, handler.request_deserializer, handler.response_serializer)

        async def unary(request, context):
            set_request_id(valid_request_id(incoming))
            started = time.perf_counter()
            try:
                result = await handler.unary_unary(request, context)
            except (Exception, asyncio.CancelledError) as e:
                failed(started, context, e)
                raise
            line(started, context.code() or grpc.StatusCode.OK)
            return result

        return grpc.unary_unary_rpc_method_handler(unary, handler.request_deserializer, handler.response_serializer)


class Service:
    def __init__(self, name: str, pool: AsyncConnectionPool, auth: AuthInterceptor | None = None):
        self.name = name
        self.pool = pool
        # Every call must carry a valid service token (see svcauth) before any servicer runs; only health Check is open.
        # Raises when a SVC_KEY_* secret is missing, so a mis-configured service refuses to start instead of running open.
        self.server = grpc.aio.server(interceptors=[RequestIdInterceptor(), auth or AuthInterceptor.for_service(name)])
        self._health = health.aio.HealthServicer()
        health_pb2_grpc.add_HealthServicer_to_server(self._health, self.server)
        self._grpc_names: list[str] = [""]  # "" is the whole-server health entry
        self._background: list[Callable[[], Coroutine[Any, Any, None]]] = []
        self._tasks: list[asyncio.Task] = []
        self._closers: list[Callable[[], Any]] = []
        self._pre_stops: list[Callable[[], Any]] = []
        self.port = 0

    def register(self, add_to_server: Callable, servicer: Any, grpc_name: str) -> None:
        """Attach a servicer and expose its fully-qualified name to grpc.health.v1."""
        add_to_server(servicer, self.server)
        self._grpc_names.append(grpc_name)

    def run_in_background(self, coro_fn: Callable[[], Coroutine[Any, Any, None]]) -> None:
        """A loop (sweeper, worker) started with the server and cancelled before it stops."""
        self._background.append(coro_fn)

    def add_pre_stop(self, hook: Callable[[], Any]) -> None:
        """Awaitable run at the start of stop(), BEFORE the gRPC server stops: end long-lived streams here, so shutdown
        does not depend on gRPC cancelling them."""
        self._pre_stops.append(hook)

    def add_closer(self, closer: Callable[[], Any]) -> None:
        """Awaitable cleanup run during stop(), after background tasks are cancelled and before the pool closes."""
        self._closers.append(closer)

    def bind(self, address: str) -> int:
        self.port = self.server.add_insecure_port(address)
        if self.port == 0:
            raise RuntimeError(f"{self.name}: could not bind {address}")
        return self.port

    async def start(self) -> None:
        await self.server.start()
        self._tasks = [asyncio.create_task(fn(), name=f"{self.name}-bg") for fn in self._background]
        for n in self._grpc_names:
            await self._health.set(n, health_pb2.HealthCheckResponse.SERVING)
        log.info("%s serving on port %s", self.name, self.port)

    async def stop(self, grace: float = 1.0) -> None:
        for n in self._grpc_names:
            await self._health.set(n, health_pb2.HealthCheckResponse.NOT_SERVING)
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        for hook in self._pre_stops:
            await hook()
        try:
            # grpc.aio's Server.stop() can wait forever on an open streaming call; a stuck shutdown must not wedge the process.
            await asyncio.wait_for(self.server.stop(grace), timeout=grace + STOP_PATIENCE_SECONDS)
        except asyncio.TimeoutError:
            log.warning("%s: the gRPC server did not stop within %.1fs; continuing the shutdown", self.name, grace + STOP_PATIENCE_SECONDS)
        for closer in self._closers:
            await closer()
        await self.pool.close()
        log.info("%s stopped", self.name)

    async def serve_forever(self) -> None:
        """Run until SIGINT/SIGTERM (or task cancellation, which is how Ctrl+C arrives on Windows)."""
        await self.start()
        stopping = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError, RuntimeError):  # Windows selector loops have no signal handlers
                loop.add_signal_handler(sig, stopping.set)
        try:
            await stopping.wait()
        finally:
            await asyncio.shield(self.stop())
