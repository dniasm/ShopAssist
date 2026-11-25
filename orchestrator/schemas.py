"""Response and request models. They are the API contract: `/openapi.json` is built from them and the
frontend generates its TypeScript types from that, so no response shape is written by hand anywhere else.
Money is integer cents (`*_cents`); timestamps are ISO-8601 UTC strings."""
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ErrorBody(BaseModel):
    code: Literal["invalid", "unauthenticated", "forbidden", "rate_limited", "not_found", "conflict", "out_of_stock", "payment_declined", "unavailable", "internal"]
    message: str
    details: dict | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class HealthOut(BaseModel):
    status: Literal["ok"]


class ServiceHealth(BaseModel):
    name: str
    service: str
    address: str
    status: Literal["up", "down"]
    latency_ms: int
    error: str | None


class ServicesHealthOut(BaseModel):
    all_up: bool
    checked_at: str
    services: list[ServiceHealth]


class LlmKeyStatus(BaseModel):
    key: str
    state: str
    rpm_left: int | None = None
    rpd_left: int | None = None
    tpm_left: int | None = None
    tpd_left: int | None = None
    cooling_until: str | None = None


class LlmLimits(BaseModel):
    rpm: int
    rpd: int
    tpm: int
    tpd: int


class LlmStatusOut(BaseModel):
    enabled: bool
    model: str
    limits: LlmLimits
    keys: list[LlmKeyStatus]


class User(BaseModel):
    user_id: str
    name: str
    email: str
    city: str


class SessionIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class SignUpIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=256)
    city: str = Field("", max_length=64)
    invite: str = Field("", max_length=128, description="Required when the shop has a SIGNUP_CODE; see GET /api/signups.")


class SignupsOut(BaseModel):
    signups: Literal["open", "invite"] = Field(description="'invite': creating an account needs the shop's invite code.")


class SessionOut(BaseModel):
    token: str
    user: User


class MeOut(BaseModel):
    user: User


class Sku(BaseModel):
    sku_id: str
    product_id: str
    size: str
    color: str


class ProductBase(BaseModel):
    product_id: str
    name: str
    category: str
    brand: str
    description: str
    price_cents: int
    rating: float
    rating_count: int
    colors: list[str]
    sizes: list[str]
    created_at: str


class ProductCard(ProductBase):
    """A product as listed: no SKUs, plus whether any SKU can be bought now."""

    in_stock: bool


class Product(ProductBase):
    skus: list[Sku]


class FacetValue(BaseModel):
    value: str
    count: int


class Facet(BaseModel):
    field: Literal["category", "brand", "color", "size"]
    values: list[FacetValue]


class ProductListResponse(BaseModel):
    products: list[ProductCard]
    total: int
    next_page_token: str
    facets: list[Facet]
    price_min_cents: int
    price_max_cents: int
    fuzzy: bool  # true: nothing matched exactly, these are the closest matches for a probable typo


class StockOption(BaseModel):
    sku_id: str
    size: str
    color: str
    available: int


class ProductDetailResponse(BaseModel):
    product: Product
    stock: list[StockOption]


class CartLine(BaseModel):
    sku_id: str
    product_id: str
    name: str
    category: str
    size: str
    color: str
    quantity: int
    unit_price_cents: int
    line_total_cents: int
    available: int  # units in stock right now; may be less than `quantity`


class CartOut(BaseModel):
    lines: list[CartLine]
    subtotal_cents: int
    shipping_cents: int
    total_cents: int
    version: int


class CartLineIn(BaseModel):
    quantity: int = Field(ge=0, le=10, description="0 removes the line")


class CheckoutIn(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=128)
    payment_method: Literal["card_ok", "card_declined", "card_flaky"]


class CheckoutOut(BaseModel):
    order_id: str


class OrderLine(BaseModel):
    sku_id: str
    product_id: str
    name: str
    category: str
    size: str
    color: str
    quantity: int
    unit_price_cents: int
    line_total_cents: int


class OrderEvent(BaseModel):
    event_id: int
    order_id: str
    status: str
    message: str
    timestamp: str


class OrderSummary(BaseModel):
    order_id: str
    user_id: str
    status: Literal["PENDING", "CONFIRMED", "PACKED", "SHIPPED", "OUT_FOR_DELIVERY", "DELIVERED", "CANCELLED", "FAILED"]
    lines: list[OrderLine]
    subtotal_cents: int
    shipping_cents: int
    total_cents: int
    payment_id: str
    failure_reason: str
    created_at: str
    updated_at: str


class Order(OrderSummary):
    history: list[OrderEvent]


class CancelIn(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=128)


class OrderOut(BaseModel):
    order: Order


class OrdersOut(BaseModel):
    orders: list[OrderSummary]
    next_page_token: str


class ConversationOut(BaseModel):
    conversation_id: str
    title: str
    updated_at: str


class ChatMessageOut(BaseModel):
    role: str = Field(description="user | assistant")
    text: str
    cards: list[dict] = Field(description="The cards shown with this message ([] when none).")
    created_at: str


class RenameIn(BaseModel):
    title: str = Field(min_length=1, max_length=80, description="Trimmed first; 1-80 characters after that.")

    @field_validator("title", mode="before")
    @classmethod
    def _strip(cls, v):
        return v.strip() if isinstance(v, str) else v
