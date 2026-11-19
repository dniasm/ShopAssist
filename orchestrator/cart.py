"""The priced view of a user's cart: names and prices from the Catalog, live availability from Inventory.

The cart itself (SKU + quantity) lives in the Customer service; everything else is looked up here, per request,
so a price change or a sell-out shows up immediately.
"""
import asyncio

from orchestrator.clients import Clients
from shopassist_proto import shopassist_pb2 as pb

# The Order service applies the real rule at checkout (services/order/saga.py); this copy only previews it.
# tests/test_api_shop.py checks that the preview equals what an order is charged.
SHIPPING_CENTS = 499
FREE_SHIPPING_FROM_CENTS = 5000


def shipping_for(subtotal_cents: int) -> int:
    return 0 if subtotal_cents == 0 or subtotal_cents >= FREE_SHIPPING_FROM_CENTS else SHIPPING_CENTS


async def priced_cart(clients: Clients, cart: pb.Cart) -> dict:
    """Cart lines with name, size, colour, price and availability. Lines whose SKU left the catalog are dropped."""
    sku_ids = [line.sku_id for line in cart.lines]
    lines: list[dict] = []
    if sku_ids:
        skus, stock = await asyncio.gather(
            clients.call("catalog", clients.catalog.GetSkus, pb.GetSkusRequest(sku_ids=sku_ids)),
            clients.call("inventory", clients.inventory.GetStock, pb.GetStockRequest(sku_ids=sku_ids)),
        )
        known = {item.sku.sku_id: item for item in skus.items}
        available = {level.sku_id: level.available for level in stock.levels}
        for line in cart.lines:
            item = known.get(line.sku_id)
            if item is None:
                continue
            lines.append({
                "sku_id": line.sku_id, "product_id": item.sku.product_id, "name": item.name, "category": item.category, "size": item.sku.size,
                "color": item.sku.color, "quantity": line.quantity, "unit_price_cents": item.price_cents,
                "line_total_cents": line.quantity * item.price_cents, "available": available.get(line.sku_id, 0),
            })
    subtotal = sum(l["line_total_cents"] for l in lines)
    shipping = shipping_for(subtotal)
    return {"lines": lines, "subtotal_cents": subtotal, "shipping_cents": shipping,
            "total_cents": subtotal + shipping, "version": cart.version}
