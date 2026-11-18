"""Public catalog endpoints: search/browse with facets, and product detail with live per-SKU stock."""
from typing import Literal

from fastapi import APIRouter, Depends, Query

from orchestrator import serialize
from orchestrator.clients import Clients
from orchestrator.deps import get_clients
from orchestrator.schemas import ProductDetailResponse, ProductListResponse
from shopassist_proto import shopassist_pb2 as pb

router = APIRouter()

SORTS = {
    "relevance": pb.SORT_RELEVANCE, "price_asc": pb.SORT_PRICE_ASC, "price_desc": pb.SORT_PRICE_DESC,
    "rating": pb.SORT_RATING, "newest": pb.SORT_NEWEST,
}


async def stock_by_product(clients: Clients, product_ids: list[str]) -> dict[str, int]:
    """Units available per product from ONE batched GetStock (never one call per card)."""
    if not product_ids:
        return {}
    reply = await clients.call("inventory", clients.inventory.GetStock, pb.GetStockRequest(product_ids=product_ids))
    totals: dict[str, int] = {}
    for level in reply.levels:
        totals[level.product_id] = totals.get(level.product_id, 0) + level.available
    return totals


@router.get("/products", response_model=ProductListResponse)
async def list_products(
    q: str = Query("", max_length=200),
    category: list[str] = Query([]),
    brand: list[str] = Query([]),
    color: list[str] = Query([]),
    size: list[str] = Query([]),
    min_price_cents: int = Query(0, ge=0),
    max_price_cents: int = Query(0, ge=0),
    sort: Literal["relevance", "price_asc", "price_desc", "rating", "newest"] = "relevance",
    page_size: int = Query(24, ge=1, le=100),
    page_token: str = Query("", max_length=200),
    clients: Clients = Depends(get_clients),
):
    reply = await clients.call("catalog", clients.catalog.ListProducts, pb.ListProductsRequest(
        query=q, categories=category, brands=brand, colors=color, sizes=size,
        min_price_cents=min_price_cents, max_price_cents=max_price_cents,
        sort=SORTS[sort], page_size=page_size, page_token=page_token,
    ))
    available = await stock_by_product(clients, [p.product_id for p in reply.products])
    return {
        "products": [{**serialize.product(p), "in_stock": available.get(p.product_id, 0) > 0} for p in reply.products],
        "total": reply.total, "next_page_token": reply.next_page_token,
        "facets": [serialize.facet(f) for f in reply.facets],
        "price_min_cents": reply.price_min_cents, "price_max_cents": reply.price_max_cents,
        "fuzzy": reply.fuzzy,
    }


@router.get("/products/{product_id}", response_model=ProductDetailResponse)
async def get_product(product_id: str, clients: Clients = Depends(get_clients)):
    p = await clients.call("catalog", clients.catalog.GetProduct, pb.GetProductRequest(product_id=product_id))
    stock = await clients.call("inventory", clients.inventory.GetStock, pb.GetStockRequest(product_ids=[product_id]))
    available = {level.sku_id: level.available for level in stock.levels}
    return {
        "product": {**serialize.product(p), "skus": [serialize.sku(s) for s in p.skus]},
        "stock": [{"sku_id": s.sku_id, "size": s.size, "color": s.color, "available": available.get(s.sku_id, 0)} for s in p.skus],
    }
