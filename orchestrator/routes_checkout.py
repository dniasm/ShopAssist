"""POST /api/checkout: start the saga, return the order id as soon as the Order service names it.

Progress is NOT in this response: it goes to the user's WebSocket (channel `checkout`), because the saga can
take seconds and the client may reconnect meanwhile.
"""
import asyncio

import grpc
import grpc.aio
from fastapi import APIRouter, Depends, Request

from orchestrator import checkout_flow
from orchestrator.clients import USER_HEADER, Clients
from orchestrator.deps import current_user_id, get_clients
from orchestrator.errors import ApiError, from_grpc
from orchestrator.schemas import CheckoutIn, CheckoutOut
from shopassist_proto import shopassist_pb2 as pb

router = APIRouter()


@router.post("/checkout", status_code=202, response_model=CheckoutOut)
async def checkout(
    body: CheckoutIn, request: Request, user_id: str = Depends(current_user_id), clients: Clients = Depends(get_clients)
):
    return {"order_id": await start_checkout(request.app.state, clients, user_id, body.idempotency_key, body.payment_method)}


async def start_checkout(state, clients: Clients, user_id: str, idempotency_key: str, payment_method: str) -> str:
    """Start the saga for the user's cart and return the order id. The one code path behind POST /api/checkout AND the
    assistant's confirmed proposals, so a confirmed checkout behaves exactly like a click on the checkout page."""
    cart = await clients.call("customer", clients.customer.GetCart, pb.GetCartRequest(), user_id=user_id)
    # An empty cart is still sent: if this key already made an order (the cart was cleared by it), the Order
    # service replays that order; if the key is new it answers INVALID_ARGUMENT, which becomes "cart is empty".
    call = clients.order.Checkout(
        pb.CheckoutRequest(
            idempotency_key=idempotency_key, payment_method=payment_method,
            lines=[pb.CheckoutLine(sku_id=l.sku_id, quantity=l.quantity) for l in cart.lines],
        ),
        metadata=((USER_HEADER, user_id),),
    )
    try:
        first = await asyncio.wait_for(call.read(), state.settings.checkout_first_event_timeout)
    except grpc.aio.AioRpcError as e:
        if e.code() == grpc.StatusCode.INVALID_ARGUMENT and not cart.lines:
            raise ApiError("invalid", "Your cart is empty.") from e
        raise from_grpc(e, "order") from e
    except asyncio.TimeoutError as e:
        call.cancel()
        raise ApiError("unavailable", "The order service is not responding.", {"service": "order"}) from e
    if first is grpc.aio.EOF:
        raise ApiError("internal", "The order service ended the checkout without an order.")
    checkout_flow.spawn(state, user_id, call, first, cart.version)
    return first.order_id
