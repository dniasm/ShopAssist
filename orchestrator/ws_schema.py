"""Types of everything the session WebSocket (`/api/ws`) can send.

WebSockets are not part of OpenAPI, so these models are exposed through one route that exists only for its
schema (`GET /api/ws/schema`, see routes_ws.py). The frontend generates its TypeScript types from `/openapi.json`
like everything else, and a test validates real emitted messages against `WsMessage`.
"""
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

from orchestrator.schemas import Order, User


class Shortage(BaseModel):
    sku_id: str
    requested: int
    available: int


class _Envelope(BaseModel):
    seq: int = Field(description="Per-user, strictly increasing. 0 for system messages, which are not in the replay log.")
    ts: str


# ---- system ----
class HelloData(BaseModel):
    user: User | None = Field(description="null for a signed-out visitor (authenticated with a guest id)")
    seq: int = Field(description="The user's latest event number; reconnect with /api/ws?after=<last seq you saw>.")


class EmptyData(BaseModel):
    pass


class AuthOkData(BaseModel):
    guest: bool = Field(description="True when the socket was authenticated as a signed-out visitor (`{type: auth, guest}`).")


class SystemAuthOk(_Envelope):
    """Reply to the client's first message `{"type":"auth","token":...}` or `{"type":"auth","guest":...}`. Nothing else is sent
    (and no other client message is read) before this; after it comes `hello`, then the replay. Anything else closes with 4401."""

    channel: Literal["system"]
    type: Literal["auth_ok"]
    data: AuthOkData


class SystemHello(_Envelope):
    channel: Literal["system"]
    type: Literal["hello"]
    data: HelloData


class SystemResync(_Envelope):
    """Your cursor is older than the log (or newer than the server): refetch state instead of trusting the stream."""

    channel: Literal["system"]
    type: Literal["resync"]
    data: EmptyData


# ---- checkout ----
class CheckoutStepData(BaseModel):
    order_id: str
    step: Literal["VALIDATING", "RESERVING", "CHARGING", "CONFIRMING", "COMPENSATING"]
    state: Literal["STARTED", "SUCCEEDED", "FAILED"]
    message: str
    shortages: list[Shortage] | None = None


class CheckoutDoneData(BaseModel):
    order: Order


class CheckoutFailedData(BaseModel):
    order_id: str
    reason: str
    shortages: list[Shortage] | None = None


class CheckoutInterruptedData(BaseModel):
    order_id: str
    message: str


class CheckoutStep(_Envelope):
    channel: Literal["checkout"]
    type: Literal["step"]
    data: CheckoutStepData


class CheckoutDone(_Envelope):
    channel: Literal["checkout"]
    type: Literal["done"]
    data: CheckoutDoneData


class CheckoutFailed(_Envelope):
    """The checkout definitely did not happen: nothing is charged, nothing is held."""

    channel: Literal["checkout"]
    type: Literal["failed"]
    data: CheckoutFailedData


class CheckoutInterrupted(_Envelope):
    """We lost contact mid-checkout. The outcome is UNKNOWN and recovery will settle it; never show success or failure."""

    channel: Literal["checkout"]
    type: Literal["interrupted"]
    data: CheckoutInterruptedData


# ---- order (sent from phase 5.3) ----
class OrderStatusData(BaseModel):
    order_id: str
    status: str
    message: str
    event_id: int


class OrderStatus(_Envelope):
    channel: Literal["order"]
    type: Literal["status"]
    data: OrderStatusData


# ---- chat (the assistant) ----
class ChatCard(BaseModel):
    """What the UI shows under a message. kind: products | product | stock | cart | order | orders | signin; `data` is that
    kind's payload (the same shapes as the REST responses: ProductListResponse items, ProductDetailResponse, CartOut, Order...)."""

    kind: Literal["products", "product", "stock", "cart", "order", "orders", "signin"]
    data: dict


class ChatTurnData(BaseModel):
    turn_id: str


class ChatToolCallData(ChatTurnData):
    tool: str
    args: dict


class ChatToolResultData(ChatTurnData):
    tool: str
    summary: str


class ChatMessageData(ChatTurnData):
    text: str
    cards: list[ChatCard]
    closing: str = Field("", description="Shown after the cards, e.g. a follow-up question under a product list.")


class ChatProposalData(ChatTurnData):
    proposal_id: str
    action: Literal["checkout", "cancel_order"]
    args: dict
    summary: str


class ChatTurnDoneData(ChatTurnData):
    source: Literal["llm", "rules"]


class ChatErrorData(ChatTurnData):
    message: str


class _Chat(_Envelope):
    channel: Literal["chat"]


class ChatTurnStarted(_Chat):
    type: Literal["turn_started"]
    data: ChatTurnData


class ChatToolCall(_Chat):
    type: Literal["tool_call"]
    data: ChatToolCallData


class ChatToolResult(_Chat):
    type: Literal["tool_result"]
    data: ChatToolResultData


class ChatMessage(_Chat):
    type: Literal["message"]
    data: ChatMessageData


class ChatProposal(_Chat):
    """Something the person must confirm: POST /api/chat/proposals/{proposal_id}/confirm (or /dismiss)."""

    type: Literal["proposal"]
    data: ChatProposalData


class ChatTurnDone(_Chat):
    type: Literal["turn_done"]
    data: ChatTurnDoneData


class ChatError(_Chat):
    type: Literal["error"]
    data: ChatErrorData


WsMessage = Annotated[
    Union[SystemAuthOk, SystemHello, SystemResync, CheckoutStep, CheckoutDone, CheckoutFailed, CheckoutInterrupted, OrderStatus,
          ChatTurnStarted, ChatToolCall, ChatToolResult, ChatMessage, ChatProposal, ChatTurnDone, ChatError],
    Field(discriminator="type"),
]
