"""The chat endpoints: start a turn, confirm or dismiss a proposal. Progress and answers arrive on the session WebSocket."""
import asyncio
import json
import secrets

from fastapi import APIRouter, Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from orchestrator.clients import Clients
from orchestrator.deps import current_user_id, get_clients
from orchestrator.errors import ApiError
from orchestrator.routes_orders import cancel_for
from orchestrator.schemas import ChatMessageOut, ConversationOut, RenameIn
from shopassist_proto import shopassist_pb2 as pb

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
        if proposal.action == "checkout_redirect":  # going to the checkout page is all this does: the person places the order there
            state.hub.publish(user_id, "chat", "navigate", {"turn_id": proposal.turn_id, "path": "/checkout"})
            text, cards = "Taking you to checkout.", []
        elif proposal.action == "cancel_order":
            order = await cancel_for(clients, user_id, proposal.args["order_id"], proposal.proposal_id)
            text, cards = f"Done: order {order['order_id']} is cancelled and your card is refunded.", [{"kind": "order", "data": {"order": order}}]
        else:
            raise ApiError("invalid", "That proposal cannot be confirmed.")
    except ApiError:
        state.proposals.release(proposal)  # nothing happened: the person can try again
        raise
    state.hub.publish(user_id, "chat", "message", {"turn_id": proposal.turn_id, "text": text, "cards": cards})
    return {"ok": True}


@router.post("/chat/proposals/{proposal_id}/dismiss", response_model=OkOut)
async def dismiss(proposal_id: str, request: Request, user_id: str = Depends(current_user_id)):
    request.app.state.proposals.dismiss(proposal_id, user_id)
    return {"ok": True}


def _cards(cards_json: str) -> list:
    try:
        cards = json.loads(cards_json) if cards_json else []
    except ValueError:
        return []
    return cards if isinstance(cards, list) else []


def _conversation(c: pb.Conversation) -> dict:
    return {"conversation_id": c.conversation_id, "title": c.title, "updated_at": c.updated_at}


@router.get("/chat/conversations", response_model=list[ConversationOut])
async def list_conversations(user_id: str = Depends(current_user_id), clients: Clients = Depends(get_clients)):
    """Saved chats, most recently updated first."""
    reply = await clients.call("assistant", clients.assistant.ListConversations, pb.ListConversationsRequest(page_size=50), user_id=user_id)
    return [_conversation(c) for c in reply.conversations]


@router.get("/chat/conversations/{conversation_id}/messages", response_model=list[ChatMessageOut])
async def list_messages(conversation_id: str, user_id: str = Depends(current_user_id), clients: Clients = Depends(get_clients)):
    """One chat's messages, oldest first (the last 200)."""
    reply = await clients.call("assistant", clients.assistant.ListMessages,
                               pb.ListMessagesRequest(conversation_id=conversation_id, limit=200), user_id=user_id)
    return [{"role": m.role, "text": m.text, "cards": _cards(m.cards_json), "created_at": m.created_at} for m in reply.messages]


@router.patch("/chat/conversations/{conversation_id}", response_model=ConversationOut)
async def rename_conversation(conversation_id: str, body: RenameIn, user_id: str = Depends(current_user_id), clients: Clients = Depends(get_clients)):
    c = await clients.call("assistant", clients.assistant.RenameConversation,
                           pb.RenameConversationRequest(conversation_id=conversation_id, title=body.title), user_id=user_id)
    return _conversation(c)


@router.delete("/chat/conversations/{conversation_id}", response_model=OkOut)
async def delete_conversation(conversation_id: str, user_id: str = Depends(current_user_id), clients: Clients = Depends(get_clients)):
    await clients.call("assistant", clients.assistant.DeleteConversation, pb.DeleteConversationRequest(conversation_id=conversation_id), user_id=user_id)
    return {"ok": True}
