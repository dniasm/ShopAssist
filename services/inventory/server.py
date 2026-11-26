"""InventoryService (:50052): gRPC translation over services/inventory/db.py. Run: python -m services.inventory.server"""
import asyncio
import logging
import os
from pathlib import Path

import grpc
from dotenv import load_dotenv

from services.common.auth import user_field
from services.common.db import create_pool, database_url, safe_target
from services.common.idempotency import KeyReused
from services.common.migrate import apply_migrations
from services.common.seed import read_seed, seed_dir as default_seed_dir, seed_if_empty
from services.common.server import Service, listen_address
from services.common.util import iso
from services.inventory import db
from shopassist_proto import shopassist_pb2 as pb
from shopassist_proto import shopassist_pb2_grpc as pb_grpc
from shared_common.common.log import audit, setup_logging

log = logging.getLogger("inventory")
MIGRATIONS = Path(__file__).parent / "migrations"
DEFAULT_TTL_SECONDS = 600
DEFAULT_SWEEP_SECONDS = 5.0


def merge_lines(lines) -> dict[str, int]:
    """Collapse repeated SKUs by summing, rejecting empty requests and non-positive quantities."""
    if not lines:
        raise ValueError("at least one line is required")
    merged: dict[str, int] = {}
    for line in lines:
        if not line.sku_id or line.quantity <= 0:
            raise ValueError("every line needs a sku_id and a quantity > 0")
        merged[line.sku_id] = merged.get(line.sku_id, 0) + line.quantity
    return merged


def to_level(row: dict) -> pb.StockLevel:
    return pb.StockLevel(
        sku_id=row["sku_id"], product_id=row["product_id"], on_hand=row["on_hand"],
        reserved=row["reserved"], available=row["available"],
    )


class InventoryServicer(pb_grpc.InventoryServiceServicer):
    def __init__(self, pool, default_ttl: int):
        self.pool = pool
        self.default_ttl = default_ttl

    async def GetStock(self, request, context):
        rows = await db.get_stock(self.pool, list(request.sku_ids), list(request.product_ids))
        return pb.GetStockResponse(levels=[to_level(r) for r in rows])

    async def Reserve(self, request, context):
        try:
            if not request.reservation_id:
                raise ValueError("reservation_id is required")
            lines = merge_lines(request.lines)
        except ValueError as e:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(e))
        try:
            result = await db.reserve(self.pool, request.reservation_id, lines, request.ttl_seconds or self.default_ttl)
        except KeyReused as e:
            await context.abort(grpc.StatusCode.ALREADY_EXISTS, str(e))
        except db.WrongState as e:
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, str(e))
        if result.created and result.ok:
            audit("stock_reserved", order=request.reservation_id, skus=",".join(sorted(lines)), **user_field(context))
        return pb.ReserveResponse(
            ok=result.ok, expires_at=iso(result.expires_at),
            shortages=[pb.Shortage(sku_id=s.sku_id, requested=s.requested, available=s.available) for s in result.shortages],
        )

    async def Commit(self, request, context):
        try:
            await db.commit(self.pool, request.reservation_id)
        except db.ReservationNotFound:
            await context.abort(grpc.StatusCode.NOT_FOUND, f"reservation {request.reservation_id} not found")
        except db.WrongState as e:
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, str(e))
        return pb.CommitResponse()

    async def Release(self, request, context):
        try:
            skus = await db.release(self.pool, request.reservation_id, request.reason)
        except db.WrongState as e:
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, str(e))
        if skus:
            audit("stock_released", order=request.reservation_id, skus=",".join(skus), **user_field(context))
        return pb.ReleaseResponse()

    async def Restock(self, request, context):
        try:
            if not request.idempotency_key:
                raise ValueError("idempotency_key is required")
            lines = merge_lines(request.lines)
        except ValueError as e:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(e))
        try:
            applied = await db.restock(self.pool, request.idempotency_key, lines)
        except KeyReused as e:
            await context.abort(grpc.StatusCode.ALREADY_EXISTS, str(e))
        except db.UnknownSku as e:
            await context.abort(grpc.StatusCode.NOT_FOUND, f"unknown sku {e}")
        if applied:
            order = {"order": request.idempotency_key.removeprefix("cancel:")} if request.idempotency_key.startswith("cancel:") else {}
            for sku_id, quantity in sorted(lines.items()):
                audit("restock", sku=sku_id, qty=quantity, **order, **user_field(context))
        return pb.RestockResponse()


async def sweep_forever(pool, interval: float) -> None:
    """Expire lapsed holds every `interval` seconds; a failed pass is logged and retried, never fatal."""
    while True:
        await asyncio.sleep(interval)
        try:
            if n := await db.expire_due(pool):
                log.info("expired %d reservation(s)", n)
        except Exception:
            log.exception("reservation sweep failed")


async def create_service(
    url: str, *, seed_dir: Path | None = None, address: str = "127.0.0.1:0",
    default_ttl: int | None = None, sweep_seconds: float = DEFAULT_SWEEP_SECONDS,
) -> Service:
    pool = await create_pool(url)
    await apply_migrations(pool, MIGRATIONS)
    rows = read_seed(seed_dir or default_seed_dir(), "inventory.json")
    if rows is not None:
        await seed_if_empty(pool, "stock", lambda conn: db.load_seed(conn, rows))
    ttl = default_ttl or int(os.environ.get("RESERVATION_TTL_SECONDS") or DEFAULT_TTL_SECONDS)
    service = Service("inventory", pool)
    service.register(pb_grpc.add_InventoryServiceServicer_to_server, InventoryServicer(pool, ttl), "shopassist.v2.InventoryService")
    service.run_in_background(lambda: sweep_forever(pool, sweep_seconds))
    service.bind(address)
    return service


async def main() -> None:
    load_dotenv()
    setup_logging("inventory")
    url = database_url("INVENTORY_DATABASE_URL")
    log.info("database %s", safe_target(url))
    service = await create_service(url, address=listen_address("INVENTORY_ADDR", "localhost:50052"))
    await service.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
    except KeyboardInterrupt:
        pass
