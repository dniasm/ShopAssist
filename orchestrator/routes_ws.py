"""The session WebSocket: one per signed-in tab, server -> client only.

Envelope `{"seq","channel","type","data","ts"}`. Messages with seq 0 (`system/hello`, `system/resync`) are
not part of the per-user log; every logged event has a seq that only increases.
"""
import asyncio
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from fastapi import APIRouter, Response, WebSocket, WebSocketDisconnect

from orchestrator import serialize
from orchestrator.errors import ApiError
from orchestrator.ws_schema import WsMessage
from shopassist_proto import shopassist_pb2 as pb

router = APIRouter()
GUEST_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")
UNAUTHENTICATED_CLOSE = 4401
TRY_AGAIN_LATER_CLOSE = 1013
POLICY_CLOSE = 1008  # a refused Origin: closed before the handshake completes, so the browser sees a failed handshake (403)
MAX_AUTH_MESSAGE_CHARS = 4096


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


def origin_allowed(websocket: WebSocket, allowed: tuple[str, ...]) -> bool:
    """Browsers always send Origin on a WebSocket handshake, so a page on another site is recognisable. Allowed: the
    orchestrator's own host (the SPA is served from it) and `allowed`. No Origin header = not a browser = let through
    (the first-message auth is still required)."""
    origin = websocket.headers.get("origin")
    if origin is None:
        return True
    host = (websocket.headers.get("host") or "").lower()
    return origin in allowed or bool(host) and urlsplit(origin).netloc.lower() == host


async def read_auth(websocket: WebSocket, timeout: float) -> dict | None:
    """The client's first message as a dict, or None when it does not arrive in time, is not a text JSON object of a
    sane size, or is not `{"type": "auth", ...}`. This is the ONLY client message read before authentication."""
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout)
        if len(raw) > MAX_AUTH_MESSAGE_CHARS:
            return None
        message = json.loads(raw)
    except (asyncio.TimeoutError, ValueError, KeyError, RuntimeError):  # no message / not JSON / a binary frame
        return None
    return message if isinstance(message, dict) and message.get("type") == "auth" else None


@router.websocket("/ws")
async def session_socket(websocket: WebSocket, after: int | None = None):
    """Connect to `/api/ws?after=<n>` (no credentials in the URL), then send ONE first message within
    `ws_auth_timeout_seconds`: `{"type":"auth","token":"<session token>"}` for a signed-in tab, or
    `{"type":"auth","guest":"<id>"}` for a signed-out visitor (chat events only, no orders). The server answers
    `system/auth_ok`, then `hello` and the replay. Anything else closes with 4401."""
    state = websocket.app.state
    settings = state.settings
    if not origin_allowed(websocket, settings.ws_allowed_origins):
        await websocket.close(code=POLICY_CLOSE, reason="origin not allowed")
        return
    await websocket.accept()  # accepted first so the browser can read our close code (4401), not just a failed handshake
    auth = await read_auth(websocket, settings.ws_auth_timeout_seconds)
    if auth is None:
        await websocket.close(code=UNAUTHENTICATED_CLOSE, reason="expected an auth message first")
        return
    token, guest = auth.get("token"), auth.get("guest")
    user = None
    try:
        if isinstance(token, str) and token:
            owner = state.tokens.verify(token)
            user = await state.clients.call("customer", state.clients.customer.GetUser, pb.GetUserRequest(user_id=owner), user_id=owner)
        elif isinstance(guest, str) and GUEST_ID.fullmatch(guest):
            owner = f"guest:{guest}"
        else:
            raise ApiError("unauthenticated", "Invalid credentials.")
    except ApiError as e:
        if e.code in ("unauthenticated", "not_found"):
            await websocket.close(code=UNAUTHENTICATED_CLOSE, reason="invalid credentials")
        else:
            await websocket.close(code=TRY_AGAIN_LATER_CLOSE, reason="try again later")
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
        await websocket.send_json(system_message("auth_ok", {"guest": user is None}))
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
