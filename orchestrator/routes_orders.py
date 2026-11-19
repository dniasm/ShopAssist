"""Order history endpoints. Ownership is enforced by the Order service; another user's order is just not found."""
from fastapi import APIRouter, Depends, Query

from orchestrator import serialize
from orchestrator.clients import Clients
from orchestrator.deps import current_user_id, get_clients
from orchestrator.schemas import CancelIn, OrderOut, OrdersOut
from shopassist_proto import shopassist_pb2 as pb

router = APIRouter()


@router.get("/orders", response_model=OrdersOut)
async def list_orders(
    page_size: int = Query(20, ge=1, le=100), page_token: str = Query("", max_length=200),
    user_id: str = Depends(current_user_id), clients: Clients = Depends(get_clients),
):
    reply = await clients.call(
        "order", clients.order.ListOrders, pb.ListOrdersRequest(page_size=page_size, page_token=page_token), user_id=user_id
    )
    return {"orders": [serialize.order_summary(o) for o in reply.orders], "next_page_token": reply.next_page_token}


@router.get("/orders/{order_id}", response_model=OrderOut)
async def get_order(order_id: str, user_id: str = Depends(current_user_id), clients: Clients = Depends(get_clients)):
    order = await clients.call("order", clients.order.GetOrder, pb.GetOrderRequest(order_id=order_id), user_id=user_id)
    return {"order": serialize.order(order)}


@router.post("/orders/{order_id}/cancel", response_model=OrderOut)
async def cancel_order(order_id: str, body: CancelIn, user_id: str = Depends(current_user_id), clients: Clients = Depends(get_clients)):
    """Cancel a CONFIRMED or PACKED order (stock back, card refunded). Anything else is 409 `conflict`; a retry with the same key is safe."""
    return {"order": await cancel_for(clients, user_id, order_id, body.idempotency_key)}


async def cancel_for(clients: Clients, user_id: str, order_id: str, idempotency_key: str) -> dict:
    """The one code path behind POST /api/orders/{id}/cancel AND the assistant's confirmed cancel proposals."""
    order = await clients.call(
        "order", clients.order.CancelOrder, pb.CancelOrderRequest(order_id=order_id, idempotency_key=idempotency_key), user_id=user_id
    )
    return serialize.order(order)
