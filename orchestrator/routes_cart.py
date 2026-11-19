"""Cart endpoints (signed-in). The Customer service stores the cart; prices and stock are looked up per request."""
from fastapi import APIRouter, Depends

from orchestrator.cart import priced_cart
from orchestrator.clients import Clients
from orchestrator.deps import current_user_id, get_clients
from orchestrator.errors import ApiError
from orchestrator.schemas import CartLineIn, CartOut
from shopassist_proto import shopassist_pb2 as pb

router = APIRouter()


@router.get("/cart", response_model=CartOut)
async def get_cart(user_id: str = Depends(current_user_id), clients: Clients = Depends(get_clients)):
    cart = await clients.call("customer", clients.customer.GetCart, pb.GetCartRequest(), user_id=user_id)
    return await priced_cart(clients, cart)


@router.put("/cart/lines/{sku_id}", response_model=CartOut)
async def put_line(sku_id: str, body: CartLineIn, user_id: str = Depends(current_user_id), clients: Clients = Depends(get_clients)):
    """Set a line's quantity (0 removes it). Adding checks the SKU exists, so the cart never holds made-up items."""
    product_id = ""
    if body.quantity > 0:
        found = await clients.call("catalog", clients.catalog.GetSkus, pb.GetSkusRequest(sku_ids=[sku_id]))
        if not found.items:
            raise ApiError("not_found", f"No such item: {sku_id}")
        product_id = found.items[0].sku.product_id
    cart = await clients.call(
        "customer", clients.customer.SetCartLine,
        pb.SetCartLineRequest(sku_id=sku_id, product_id=product_id, quantity=body.quantity), user_id=user_id,
    )
    return await priced_cart(clients, cart)


@router.delete("/cart/lines/{sku_id}", response_model=CartOut)
async def delete_line(sku_id: str, user_id: str = Depends(current_user_id), clients: Clients = Depends(get_clients)):
    """Removing never needs the SKU to still exist in the catalog, so stale lines can always be cleared."""
    cart = await clients.call(
        "customer", clients.customer.SetCartLine, pb.SetCartLineRequest(sku_id=sku_id, quantity=0), user_id=user_id
    )
    return await priced_cart(clients, cart)
