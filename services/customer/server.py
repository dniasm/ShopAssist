"""CustomerService (:50054): users, their logins and their carts. Run: python -m services.customer.server

Seeded demo users have no password of their own: at start every user without one gets DEMO_PASSWORD (default
"shopassist-demo"; empty disables demo logins). Accounts made by CreateUser always have their own.
"""
import asyncio
import logging
import os
import re
from pathlib import Path

import grpc
from dotenv import load_dotenv

from services.common.auth import metadata_value, require_user
from services.common.db import create_pool, database_url, safe_target
from services.common.migrate import apply_migrations
from services.common.seed import read_seed, seed_dir as default_seed_dir, seed_if_empty
from services.common.server import Service, listen_address
from services.common.util import iso
from services.customer import db
from services.customer.passwords import MAX_PASSWORD, MIN_PASSWORD, check_password, hash_password
from shopassist_proto import shopassist_pb2 as pb
from shopassist_proto import shopassist_pb2_grpc as pb_grpc
from shared_common.common.log import audit, setup_logging

MIGRATIONS = Path(__file__).parent / "migrations"
MAX_LINE_QUANTITY = 10
SOURCE_HEADER = "x-source"  # "assistant" when the orchestrator sends the shopping assistant's edit; anything else is "web"
DEFAULT_DEMO_PASSWORD = "shopassist-demo"
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]+$")
# Checked even when the email is unknown, so a wrong email takes as long as a wrong password (no account probing).
_DUMMY_HASH = hash_password("not a real password")


def source_of(context) -> str:
    return "assistant" if metadata_value(context, SOURCE_HEADER) == "assistant" else "web"


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

    async def Authenticate(self, request, context):
        row = await db.credentials(self.pool, request.email.strip())
        ok = await asyncio.to_thread(check_password, request.password, row["password_hash"] if row else _DUMMY_HASH)
        if row is None or not ok:
            audit("login_failed", email=request.email.strip(), reason="unknown_email" if row is None else "wrong_password",
                  **({"user": row["user_id"]} if row else {}))
            await context.abort(grpc.StatusCode.UNAUTHENTICATED, "wrong email or password")
        audit("login_ok", email=row["email"], user=row["user_id"])
        return to_user(row)

    async def CreateUser(self, request, context):
        name, email, city = request.name.strip(), request.email.strip(), request.city.strip()
        if not 1 <= len(name) <= 64:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "A name is 1-64 characters.")
        if len(email) > 254 or not EMAIL.match(email):
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "That does not look like an email address.")
        if not MIN_PASSWORD <= len(request.password) <= MAX_PASSWORD:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, f"A password is {MIN_PASSWORD}-{MAX_PASSWORD} characters.")
        password_hash = await asyncio.to_thread(hash_password, request.password)
        try:
            user = await db.create_user(self.pool, name, email, city[:64], password_hash)
        except db.EmailTaken:
            await context.abort(grpc.StatusCode.ALREADY_EXISTS, "an account with that email already exists")
        audit("signup", email=user["email"], user=user["user_id"])
        return to_user(user)

    async def GetCart(self, request, context):
        user_id = await require_user(context)
        return to_cart(await self._run(context, db.get_cart(self.pool, user_id)))

    async def SetCartLine(self, request, context):
        user_id = await require_user(context)
        if not request.sku_id:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "sku_id is required")
        if not 0 <= request.quantity <= MAX_LINE_QUANTITY:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, f"quantity must be 0..{MAX_LINE_QUANTITY} (0 removes the line)")
        if request.quantity > 0 and not request.product_id:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "product_id is required")
        cart = await self._run(context, db.set_line(self.pool, user_id, request.sku_id, request.product_id, request.quantity))
        before, source = cart["qty_before"], source_of(context)
        if request.quantity == 0 and before:
            audit("cart_line_removed", user=user_id, sku=request.sku_id, qty_before=before, source=source)
        elif request.quantity != before:
            audit("cart_line_set", user=user_id, sku=request.sku_id, qty_before=before, qty_after=request.quantity, source=source)
        return to_cart(cart)

    async def ClearCart(self, request, context):
        user_id = await require_user(context)
        cart = await self._run(context, db.clear_cart(self.pool, user_id, request.if_version))
        if cart["removed"]:
            audit("cart_cleared", user=user_id, lines=cart["removed"], source=source_of(context))
        return to_cart(cart)

    @staticmethod
    async def _run(context, operation) -> dict:
        """Await a cart operation and translate its domain errors; returns the resulting cart row."""
        try:
            return await operation
        except db.UnknownUser as e:
            await context.abort(grpc.StatusCode.NOT_FOUND, f"user {e} not found")
        except db.VersionMismatch as e:
            await context.abort(grpc.StatusCode.ABORTED, str(e))


async def create_service(url: str, *, seed_dir: Path | None = None, address: str = "127.0.0.1:0",
                         demo_password: str | None = None) -> Service:
    pool = await create_pool(url)
    await apply_migrations(pool, MIGRATIONS)
    data = read_seed(seed_dir or default_seed_dir(), "customers.json")
    if data is not None:
        await seed_if_empty(pool, "users", lambda conn: db.load_seed(conn, data))
    demo = os.environ.get("DEMO_PASSWORD", DEFAULT_DEMO_PASSWORD) if demo_password is None else demo_password
    if demo:  # one hash for all of them: they share the password anyway, and start-up hashes once, not per user
        await db.set_missing_passwords(pool, await asyncio.to_thread(hash_password, demo))
    service = Service("customer", pool)
    service.register(pb_grpc.add_CustomerServiceServicer_to_server, CustomerServicer(pool), "shopassist.v2.CustomerService")
    service.bind(address)
    return service


async def main() -> None:
    load_dotenv()
    setup_logging("customer")
    url = database_url("CUSTOMER_DATABASE_URL")
    logging.getLogger("customer").info("database %s", safe_target(url))
    service = await create_service(url, address=listen_address("CUSTOMER_ADDR", "localhost:50054"))
    await service.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
    except KeyboardInterrupt:
        pass
