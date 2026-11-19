"""The session WebSocket: one per signed-in tab, server -> client only.

Envelope `{"seq","channel","type","data","ts"}`. Messages with seq 0 (`system/hello`, `system/resync`) are
not part of the per-user log; every logged event has a seq that only increases.
"""
import asyncio
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Response, WebSocket, WebSocketDisconnect

from orchestrator import serialize
from orchestrator.errors import ApiError
from orchestrator.ws_schema import WsMessage
from shopassist_proto import shopassist_pb2 as pb

router = APIRouter()
GUEST_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")
UNAUTHENTICATED_CLOSE = 4401
TRY_AGAIN_LATER_CLOSE = 1013


def system_message(type_: str, data: dict) -> dict:
    return {"seq": 0, "channel": "system", "type": type_, "data": data,
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds")}


@router.get(
    "/ws/schema", status_code=204, response_class=Response, summary="WebSocket message types (schema only)",
    responses={200: {"model": WsMessage, "description": "Never returned. Documents every message `/api/ws` can send."}},
)
async def ws_schema() -> Response:
    """Exists only so `/openapi.json` carries the WebSocket message types for the generated frontend types."""
    return Response(status_code=204)


@router.websocket("/ws")
async def session_socket(websocket: WebSocket, token: str = "", after: int | None = None, guest: str = ""):
    """`?token=` for a signed-in tab. `?guest=<id>` (no token) is a signed-out visitor: chat events only, no orders."""
    state = websocket.app.state
    await websocket.accept()  # accepted first so the browser can read our close code (4401), not just a failed handshake
    user = None
    try:
        if token or not GUEST_ID.fullmatch(guest):
            owner = state.tokens.verify(token)
            user = await state.clients.call("customer", state.clients.customer.GetUser, pb.GetUserRequest(user_id=owner), user_id=owner)
        else:
            owner = f"guest:{guest}"
    except ApiError as e:
        await websocket.close(code=UNAUTHENTICATED_CLOSE if e.code in ("unauthenticated", "not_found") else TRY_AGAIN_LATER_CLOSE)
        return
    hub = state.hub
    if user:
        state.watchers.socket_opened(owner)  # one OrderService.WatchOrders stream per user, however many tabs
    # No await between subscribe and replay: everything older is in the replay, everything newer is in the queue.
    inbox = hub.subscribe(owner)
    backlog, resync = hub.replay(owner, after)
    if user:
        await state.watchers.wait_ready(owner)  # from here on, order events cannot slip past this socket
    try:
        await websocket.send_json(system_message("hello", {"user": serialize.user(user) if user else None, "seq": hub.latest_seq(owner)}))
        if resync:
            await websocket.send_json(system_message("resync", {}))
        for event in backlog:
            await websocket.send_json(event)
        await _pump(websocket, inbox)
    except WebSocketDisconnect:
        pass
    finally:
        hub.unsubscribe(owner, inbox)
        if user:
            state.watchers.socket_closed(owner)


async def _pump(websocket: WebSocket, inbox: asyncio.Queue) -> None:
    """Send queued events until the client goes away (we never expect messages from it, but must notice a close)."""
    closed = asyncio.create_task(_wait_for_close(websocket))
    try:
        while True:
            next_event = asyncio.create_task(inbox.get())
            await asyncio.wait({next_event, closed}, return_when=asyncio.FIRST_COMPLETED)
            if closed.done():
                next_event.cancel()
                return
            await websocket.send_json(next_event.result())
    finally:
        closed.cancel()


async def _wait_for_close(websocket: WebSocket) -> None:
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return
