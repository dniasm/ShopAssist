"""CustomerService (:50054): demo users and carts. Run: python -m services.customer.server"""
import asyncio
import logging
from pathlib import Path

import grpc
from dotenv import load_dotenv

from services.common.auth import require_user
from services.common.db import create_pool, database_url, safe_target
from services.common.migrate import apply_migrations
from services.common.seed import read_seed, seed_dir as default_seed_dir, seed_if_empty
from services.common.server import Service, listen_address
from services.common.util import iso
from services.customer import db
from shopassist_proto import shopassist_pb2 as pb
from shopassist_proto import shopassist_pb2_grpc as pb_grpc

MIGRATIONS = Path(__file__).parent / "migrations"
MAX_LINE_QUANTITY = 10


def to_user(row: dict) -> pb.User:
    return pb.User(user_id=row["user_id"], name=row["name"], email=row["email"], city=row["city"])


def to_cart(row: dict) -> pb.Cart:
    return pb.Cart(
        user_id=row["user_id"], version=row["version"], updated_at=iso(row["updated_at"]),
        lines=[pb.CartLine(sku_id=l["sku_id"], product_id=l["product_id"], quantity=l["quantity"], added_at=iso(l["added_at"]))
               for l in row["lines"]],
    )


class CustomerServicer(pb_grpc.CustomerServiceServicer):
    def __init__(self, pool):
        self.pool = pool

    async def ListUsers(self, request, context):
        return pb.ListUsersResponse(users=[to_user(r) for r in await db.list_users(self.pool)])

    async def GetUser(self, request, context):
        row = await db.get_user(self.pool, request.user_id)
        if row is None:
            await context.abort(grpc.StatusCode.NOT_FOUND, f"user {request.user_id} not found")
        return to_user(row)

    async def GetCart(self, request, context):
        user_id = await require_user(context)
        return await self._run(context, db.get_cart(self.pool, user_id))

    async def SetCartLine(self, request, context):
        user_id = await require_user(context)
        if not request.sku_id:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "sku_id is required")
        if not 0 <= request.quantity <= MAX_LINE_QUANTITY:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, f"quantity must be 0..{MAX_LINE_QUANTITY} (0 removes the line)")
        if request.quantity > 0 and not request.product_id:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "product_id is required")
        return await self._run(context, db.set_line(self.pool, user_id, request.sku_id, request.product_id, request.quantity))

    async def ClearCart(self, request, context):
        user_id = await require_user(context)
        return await self._run(context, db.clear_cart(self.pool, user_id, request.if_version))

    @staticmethod
    async def _run(context, operation) -> pb.Cart:
        """Await a cart operation and translate its domain errors; always returns the resulting cart."""
        try:
            return to_cart(await operation)
        except db.UnknownUser as e:
            await context.abort(grpc.StatusCode.NOT_FOUND, f"user {e} not found")
        except db.VersionMismatch as e:
            await context.abort(grpc.StatusCode.ABORTED, str(e))


async def create_service(url: str, *, seed_dir: Path | None = None, address: str = "127.0.0.1:0") -> Service:
    pool = await create_pool(url)
    await apply_migrations(pool, MIGRATIONS)
    data = read_seed(seed_dir or default_seed_dir(), "customers.json")
    if data is not None:
        await seed_if_empty(pool, "users", lambda conn: db.load_seed(conn, data))
    service = Service("customer", pool)
    service.register(pb_grpc.add_CustomerServiceServicer_to_server, CustomerServicer(pool), "shopassist.v2.CustomerService")
    service.bind(address)
    return service


async def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s customer %(levelname)s %(message)s")
    url = database_url("CUSTOMER_DATABASE_URL")
    logging.getLogger("customer").info("database %s", safe_target(url))
    service = await create_service(url, address=listen_address("CUSTOMER_ADDR", "localhost:50054"))
    await service.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
    except KeyboardInterrupt:
        pass
