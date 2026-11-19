"""PaymentService (:50055): simulated charges and refunds. Run: python -m services.payment.server"""
import asyncio
import logging
import os
from pathlib import Path

import grpc
from dotenv import load_dotenv

from services.common.db import create_pool, database_url, safe_target
from services.common.idempotency import KeyReused
from services.common.migrate import apply_migrations
from services.common.seed import read_seed, seed_dir as default_seed_dir, seed_if_empty
from services.common.server import Service, listen_address
from services.payment import db
from shopassist_proto import shopassist_pb2 as pb
from shopassist_proto import shopassist_pb2_grpc as pb_grpc

MIGRATIONS = Path(__file__).parent / "migrations"
DEFAULT_LATENCY_MS = 300
STATUS = {"SUCCEEDED": pb.PAYMENT_SUCCEEDED, "DECLINED": pb.PAYMENT_DECLINED, "REFUNDED": pb.PAYMENT_REFUNDED}


class PaymentServicer(pb_grpc.PaymentServiceServicer):
    def __init__(self, pool, latency_ms: int):
        self.pool = pool
        self.latency = latency_ms / 1000

    async def Charge(self, request, context):
        if not request.idempotency_key or not request.order_id:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "idempotency_key and order_id are required")
        if request.amount_cents <= 0:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "amount_cents must be positive")
        if request.payment_method not in db.METHODS:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, f"payment_method must be one of {', '.join(db.METHODS)}")
        await asyncio.sleep(self.latency)  # the card network is slow; this is also what gives races a window in tests
        try:
            row = await db.charge(self.pool, request.idempotency_key, request.order_id, request.amount_cents, request.payment_method)
        except KeyReused as e:
            await context.abort(grpc.StatusCode.ALREADY_EXISTS, str(e))
        return pb.ChargeResponse(payment_id=row["payment_id"], status=STATUS[row["status"]], decline_reason=row["decline_reason"])

    async def LookupCharge(self, request, context):
        """Lets the Order service's recovery learn the outcome of a charge without ever causing one."""
        if not request.idempotency_key:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "idempotency_key is required")
        row = await db.lookup_charge(self.pool, request.idempotency_key)
        if row is None:
            await context.abort(grpc.StatusCode.NOT_FOUND, f"no charge was made with key {request.idempotency_key}")
        return pb.ChargeResponse(payment_id=row["payment_id"], status=STATUS[row["status"]], decline_reason=row["decline_reason"])

    async def Refund(self, request, context):
        if not request.idempotency_key or not request.payment_id:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "idempotency_key and payment_id are required")
        if request.amount_cents <= 0:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "amount_cents must be positive")
        try:
            row = await db.refund(self.pool, request.idempotency_key, request.payment_id, request.amount_cents)
        except KeyReused as e:
            await context.abort(grpc.StatusCode.ALREADY_EXISTS, str(e))
        except db.PaymentNotFound:
            await context.abort(grpc.StatusCode.NOT_FOUND, f"payment {request.payment_id} not found")
        except db.NotRefundable as e:
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, str(e))
        except db.AmountTooLarge as e:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(e))
        return pb.RefundResponse(refund_id=row["refund_id"], status=pb.PAYMENT_REFUNDED)


async def create_service(
    url: str, *, seed_dir: Path | None = None, address: str = "127.0.0.1:0", latency_ms: int | None = None,
) -> Service:
    pool = await create_pool(url)
    await apply_migrations(pool, MIGRATIONS)
    payments = read_seed(seed_dir or default_seed_dir(), "payments.json")
    if payments is not None:
        await seed_if_empty(pool, "payments", lambda conn: db.load_seed(conn, payments))
    if latency_ms is None:
        latency_ms = int(os.environ.get("PAYMENT_LATENCY_MS") or DEFAULT_LATENCY_MS)
    service = Service("payment", pool)
    service.register(pb_grpc.add_PaymentServiceServicer_to_server, PaymentServicer(pool, latency_ms), "shopassist.v2.PaymentService")
    service.bind(address)
    return service


async def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s payment %(levelname)s %(message)s")
    url = database_url("PAYMENT_DATABASE_URL")
    logging.getLogger("payment").info("database %s", safe_target(url))
    service = await create_service(url, address=listen_address("PAYMENT_ADDR", "localhost:50055"))
    await service.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
    except KeyboardInterrupt:
        pass
