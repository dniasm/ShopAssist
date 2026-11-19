"""The chat endpoints: start a turn, confirm or dismiss a proposal. Progress and answers arrive on the session WebSocket."""
import asyncio
import secrets

from fastapi import APIRouter, Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from orchestrator.clients import Clients
from orchestrator.deps import current_user_id, get_clients
from orchestrator.errors import ApiError
from orchestrator.routes_checkout import start_checkout
from orchestrator.routes_orders import cancel_for

router = APIRouter()
_optional_bearer = HTTPBearer(auto_error=False)


class ChatContextIn(BaseModel):
    path: str = Field("/", max_length=200)
    product_id: str | None = Field(None, pattern=r"^P-\d+$")


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=500)
    context: ChatContextIn = ChatContextIn()
    conversation_id: str | None = Field(None, pattern=r"^[A-Za-z0-9_-]{1,64}$", description="Client-generated; a new one starts a conversation with fresh memory.")
    guest_id: str | None = Field(None, pattern=r"^[A-Za-z0-9_-]{8,64}$", description="Signed-out visitors: a random id of your own; open /api/ws?guest=<id>.")


class TurnOut(BaseModel):
    turn_id: str


class OkOut(BaseModel):
    ok: bool


def owner_of(request: Request, credentials: HTTPAuthorizationCredentials | None, guest_id: str | None) -> tuple[str, str | None]:
    """(event owner, user id). A valid session token means a user; otherwise the visitor needs a guest id of their own."""
    if credentials is not None:
        user_id = request.app.state.tokens.verify(credentials.credentials)
        return user_id, user_id
    if not guest_id:
        raise ApiError("invalid", "Sign in, or send a guest_id, so the answer has somewhere to go.")
    return f"guest:{guest_id}", None


@router.post("/chat", status_code=202, response_model=TurnOut)
async def chat(body: ChatIn, request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(_optional_bearer)):
    owner, user_id = owner_of(request, credentials, body.guest_id)
    state = request.app.state
    turn_id = "T-" + secrets.token_hex(6)
    task = asyncio.create_task(state.agent.run_turn(owner, user_id, turn_id, body.message.strip(), body.context.model_dump(), body.conversation_id or "default"))
    state.tasks.add(task)
    task.add_done_callback(state.tasks.discard)
    return {"turn_id": turn_id}


@router.post("/chat/proposals/{proposal_id}/confirm", status_code=202, response_model=OkOut)
async def confirm(proposal_id: str, request: Request, user_id: str = Depends(current_user_id), clients: Clients = Depends(get_clients)):
    """Run what the assistant proposed, AS THE USER, through the same code as the REST endpoints. Works once per proposal."""
    state = request.app.state
    proposal = state.proposals.claim(proposal_id, user_id)  # open -> confirmed, atomically: a double click cannot run it twice
    try:
        if proposal.action == "checkout":
            order_id = await start_checkout(state, clients, user_id, proposal.proposal_id, proposal.args.get("payment_method", "card_ok"))
            text, cards = f"Placing order {order_id} now. You can follow its progress live.", []
        else:
            order = await cancel_for(clients, user_id, proposal.args["order_id"], proposal.proposal_id)
            text, cards = f"Done: order {order['order_id']} is cancelled and your card is refunded.", [{"kind": "order", "data": {"order": order}}]
    except ApiError:
        state.proposals.release(proposal)  # nothing happened: the person can try again
        raise
    state.hub.publish(user_id, "chat", "message", {"turn_id": proposal.turn_id, "text": text, "cards": cards})
    return {"ok": True}


@router.post("/chat/proposals/{proposal_id}/dismiss", response_model=OkOut)
async def dismiss(proposal_id: str, request: Request, user_id: str = Depends(current_user_id)):
    request.app.state.proposals.dismiss(proposal_id, user_id)
    return {"ok": True}
