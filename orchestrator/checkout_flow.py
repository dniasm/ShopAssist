"""Relay a checkout's progress from the Order service to the user's WebSocket, and tidy the cart afterwards."""
import asyncio
import logging

import grpc
import grpc.aio

from orchestrator import serialize
from orchestrator.clients import Clients
from orchestrator.errors import ApiError
from orchestrator.events import EventHub
from shopassist_proto import shopassist_pb2 as pb

log = logging.getLogger(__name__)
INTERRUPTED = (
    "We lost contact while placing your order. It will be settled automatically within a couple of minutes, "
    "and you will only be charged if it is confirmed."
)


def step_data(event: pb.CheckoutEvent) -> dict:
    data = {
        "order_id": event.order_id, "step": pb.CheckoutStep.Name(event.step),
        "state": pb.StepState.Name(event.state).removeprefix("STEP_"), "message": event.message,
    }
    if event.shortages:
        data["shortages"] = [serialize.shortage(s) for s in event.shortages]
    return data


def final_message(event: pb.CheckoutEvent) -> tuple[str, dict]:
    """(type, data) of the closing event: `done` with the order, or `failed` with the reason (and shortages)."""
    if event.state == pb.STEP_SUCCEEDED:
        return "done", {"order": serialize.order(event.order)}
    data = {"order_id": event.order_id, "reason": event.message or event.order.failure_reason}
    if event.shortages:
        data["shortages"] = [serialize.shortage(s) for s in event.shortages]
    return "failed", data


async def forward(
    hub: EventHub, clients: Clients, forwarding: set, user_id: str, call, first: pb.CheckoutEvent, cart_version: int
) -> None:
    """Publish every CheckoutEvent on the `checkout` channel. On success the cart is cleared first, so a client
    reacting to `done` already sees an empty cart.

    A checkout that is only being REPLAYED (its first event is already the final one) does not clear the cart:
    the cart may hold new items by now.
    """
    fresh = first.step != pb.FINISHED
    event = first
    try:
        while event.step != pb.FINISHED:
            hub.publish(user_id, "checkout", "step", step_data(event))
            event = await call.read()
            if event is grpc.aio.EOF:
                raise ConnectionError("stream ended before the final event")
        if event.state == pb.STEP_SUCCEEDED and fresh:
            await clear_cart(clients, user_id, cart_version)
        hub.publish(user_id, "checkout", *final_message(event))
    except (grpc.aio.AioRpcError, ConnectionError) as e:
        log.warning("checkout %s: event stream failed: %s", first.order_id, e)
        # The outcome is UNKNOWN (recovery may still confirm the order), so this is never reported as `failed`.
        hub.publish(user_id, "checkout", "interrupted", {"order_id": first.order_id, "message": INTERRUPTED})
    finally:
        forwarding.discard((user_id, first.order_id))
        call.cancel()


async def clear_cart(clients: Clients, user_id: str, cart_version: int) -> None:
    """Empty the cart that was checked out. If it changed meanwhile (version moved) leave it: those are new items."""
    try:
        await clients.call("customer", clients.customer.ClearCart, pb.ClearCartRequest(if_version=cart_version), user_id=user_id)
    except ApiError as e:
        if e.code == "conflict":
            log.info("cart of %s changed during checkout; left as it is", user_id)
        else:
            log.warning("could not clear the cart of %s after checkout: %s", user_id, e.message)


def spawn(app_state, user_id: str, call, first: pb.CheckoutEvent, cart_version: int) -> bool:
    """Start forwarding unless this order is already being forwarded (a double submit). Returns whether it started."""
    key = (user_id, first.order_id)
    if key in app_state.forwarding:
        call.cancel()
        return False
    app_state.forwarding.add(key)
    task = asyncio.create_task(forward(app_state.hub, app_state.clients, app_state.forwarding, user_id, call, first, cart_version))
    app_state.tasks.add(task)
    task.add_done_callback(app_state.tasks.discard)
    return True
