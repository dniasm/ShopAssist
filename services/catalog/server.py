"""CatalogService (:50051): gRPC translation over services/catalog/db.py. Run: python -m services.catalog.server"""
import asyncio
import logging
from pathlib import Path

import grpc
from dotenv import load_dotenv

from services.catalog import db
from services.common.db import create_pool, database_url, safe_target
from services.common.migrate import apply_migrations
from services.common.seed import read_seed, seed_dir as default_seed_dir, seed_if_empty
from services.common.server import Service, listen_address
from services.common.util import decode_offset, encode_offset, iso, page_size
from shopassist_proto import shopassist_pb2 as pb
from shopassist_proto import shopassist_pb2_grpc as pb_grpc

MIGRATIONS = Path(__file__).parent / "migrations"
SIZE_ORDER = ["XS", "S", "M", "L", "XL", "XXL"]
SORT_NAMES = {
    pb.SORT_RELEVANCE: "relevance", pb.SORT_PRICE_ASC: "price_asc", pb.SORT_PRICE_DESC: "price_desc",
    pb.SORT_RATING: "rating", pb.SORT_NEWEST: "newest",
}


def _size_key(size: str) -> tuple:
    """Clothing sizes in S-to-XXL order, then numeric shoe sizes, then anything else (e.g. ONE)."""
    if size in SIZE_ORDER:
        return (0, SIZE_ORDER.index(size), "")
    if size.isdigit():
        return (1, int(size), "")
    return (2, 0, size)


def to_product(row: dict) -> pb.Product:
    return pb.Product(
        product_id=row["product_id"], name=row["name"], category=row["category"], brand=row["brand"],
        description=row["description"], price_cents=row["price_cents"], rating=float(row["rating"]),
        rating_count=row["rating_count"], colors=row["colors"], sizes=row["sizes"],
        skus=[pb.Sku(**s) for s in row.get("skus", [])], created_at=iso(row["created_at"]),
    )


def to_facets(counts: dict[str, dict[str, int]]) -> list[pb.Facet]:
    facets = []
    for name in db.FACET_FIELDS:
        items = list(counts[name].items())
        items.sort(key=(lambda kv: _size_key(kv[0])) if name == "size" else (lambda kv: (-kv[1], kv[0])))
        facets.append(pb.Facet(field=name, values=[pb.FacetValue(value=v, count=c) for v, c in items]))
    return facets


class CatalogServicer(pb_grpc.CatalogServiceServicer):
    def __init__(self, pool):
        self.pool = pool

    async def ListProducts(self, request, context):
        try:
            offset = decode_offset(request.page_token)
        except ValueError as e:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(e))
        filters = db.Filters(
            query=request.query, categories=list(request.categories), brands=list(request.brands),
            colors=list(request.colors), sizes=list(request.sizes),
            min_price_cents=request.min_price_cents, max_price_cents=request.max_price_cents,
        )
        limit = page_size(request.page_size)
        async with self.pool.connection() as conn:
            found = await db.search(conn, filters, SORT_NAMES[request.sort], limit, offset)
        more = offset + len(found.rows) < found.total
        return pb.ListProductsResponse(
            products=[to_product(r) for r in found.rows], total=found.total,
            next_page_token=encode_offset(offset + len(found.rows)) if more else "",
            facets=to_facets(found.counts), price_min_cents=found.price_low, price_max_cents=found.price_high,
            fuzzy=found.fuzzy,
        )

    async def GetProduct(self, request, context):
        rows = await db.get_products(self.pool, [request.product_id])
        if not rows:
            await context.abort(grpc.StatusCode.NOT_FOUND, f"product {request.product_id} not found")
        return to_product(rows[0])

    async def GetProducts(self, request, context):
        rows = await db.get_products(self.pool, list(request.product_ids))
        return pb.GetProductsResponse(products=[to_product(r) for r in rows])

    async def GetSkus(self, request, context):
        rows = await db.get_skus(self.pool, list(request.sku_ids))
        return pb.GetSkusResponse(items=[
            pb.SkuWithProduct(
                sku=pb.Sku(sku_id=r["sku_id"], product_id=r["product_id"], size=r["size"], color=r["color"]),
                name=r["name"], price_cents=r["price_cents"], category=r["category"],
            ) for r in rows
        ])


async def create_service(url: str, *, seed_dir: Path | None = None, address: str = "127.0.0.1:0") -> Service:
    """Pool, migrations, seed, servicer. Not started yet; port 0 picks a free port (tests)."""
    pool = await create_pool(url)
    await apply_migrations(pool, MIGRATIONS)
    data = read_seed(seed_dir or default_seed_dir(), "catalog.json")
    if data is not None:
        await seed_if_empty(pool, "products", lambda conn: db.load_seed(conn, data))
    service = Service("catalog", pool)
    service.register(pb_grpc.add_CatalogServiceServicer_to_server, CatalogServicer(pool), "shopassist.v2.CatalogService")
    service.bind(address)
    return service


async def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s catalog %(levelname)s %(message)s")
    url = database_url("CATALOG_DATABASE_URL")
    logging.getLogger("catalog").info("database %s", safe_target(url))
    service = await create_service(url, address=listen_address("CATALOG_ADDR", "localhost:50051"))
    await service.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
    except KeyboardInterrupt:
        pass
