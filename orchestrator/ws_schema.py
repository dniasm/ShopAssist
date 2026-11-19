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
    user: User
    seq: int = Field(description="The user's latest event number; reconnect with ?after=<last seq you saw>.")


class EmptyData(BaseModel):
    pass


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


WsMessage = Annotated[
    Union[SystemHello, SystemResync, CheckoutStep, CheckoutDone, CheckoutFailed, CheckoutInterrupted, OrderStatus],
    Field(discriminator="type"),
]
