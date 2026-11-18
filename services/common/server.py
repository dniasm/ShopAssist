"""grpc.aio server bootstrap: health service, background tasks, graceful shutdown.

A `Service` is built, started and stopped the same way in production (`serve_forever`) and in tests
(`start`/`stop` on a port-0 address), so tests exercise the real wiring.
"""
import asyncio
import contextlib
import logging
import os
import signal
from collections.abc import Callable, Coroutine
from typing import Any

import grpc
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from psycopg_pool import AsyncConnectionPool

log = logging.getLogger(__name__)


def listen_address(env_var: str, default: str) -> str:
    """Services listen on the same variable the orchestrator dials, e.g. CATALOG_ADDR=localhost:50051."""
    return os.environ.get(env_var) or default


class Service:
    def __init__(self, name: str, pool: AsyncConnectionPool):
        self.name = name
        self.pool = pool
        self.server = grpc.aio.server()
        self._health = health.aio.HealthServicer()
        health_pb2_grpc.add_HealthServicer_to_server(self._health, self.server)
        self._grpc_names: list[str] = [""]  # "" is the whole-server health entry
        self._background: list[Callable[[], Coroutine[Any, Any, None]]] = []
        self._tasks: list[asyncio.Task] = []
        self.port = 0

    def register(self, add_to_server: Callable, servicer: Any, grpc_name: str) -> None:
        """Attach a servicer and expose its fully-qualified name to grpc.health.v1."""
        add_to_server(servicer, self.server)
        self._grpc_names.append(grpc_name)

    def run_in_background(self, coro_fn: Callable[[], Coroutine[Any, Any, None]]) -> None:
        """A loop (sweeper, worker) started with the server and cancelled before it stops."""
        self._background.append(coro_fn)

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
        await self.server.stop(grace)
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
