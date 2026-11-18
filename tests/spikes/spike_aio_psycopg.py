"""Spike: do grpc.aio and a psycopg AsyncConnectionPool coexist on Windows / Python 3.14?

Why: psycopg async needs a selector event loop on Windows; grpc.aio must still serve on it.
Run: python tests/spikes/spike_aio_psycopg.py   (needs PG_ADMIN_URL in .env)
"""
import asyncio
import os
import sys

import grpc
from dotenv import load_dotenv
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from psycopg_pool import AsyncConnectionPool


async def main() -> None:
    load_dotenv()
    pool = AsyncConnectionPool(os.environ["PG_ADMIN_URL"], min_size=1, max_size=4, open=False)
    await pool.open(wait=True)

    servicer = health.aio.HealthServicer()
    server = grpc.aio.server()
    health_pb2_grpc.add_HealthServicer_to_server(servicer, server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()

    async def query_many(n: int) -> int:
        async def one(i: int) -> int:
            async with pool.connection() as conn:
                row = await (await conn.execute("SELECT %s::int + 1", (i,))).fetchone()
                return row[0]
        return sum(await asyncio.gather(*(one(i) for i in range(n))))

    async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as ch:
        stub = health_pb2_grpc.HealthStub(ch)
        # interleave real grpc calls and real db queries on the same loop
        results = await asyncio.gather(
            query_many(50),
            *(stub.Check(health_pb2.HealthCheckRequest(service=""), timeout=2) for _ in range(50)),
        )
    statuses = {health_pb2.HealthCheckResponse.ServingStatus.Name(r.status) for r in results[1:]}
    print("loop:", type(asyncio.get_running_loop()).__name__)
    print("db sum:", results[0], "(expected", sum(i + 1 for i in range(50)), ")")
    print("grpc statuses:", statuses)
    await server.stop(0)
    await pool.close()
    assert results[0] == sum(i + 1 for i in range(50)) and statuses == {"SERVING"}
    print("SPIKE OK")


if __name__ == "__main__":
    asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
    sys.exit(0)
