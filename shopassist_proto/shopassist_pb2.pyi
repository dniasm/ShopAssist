from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class SortBy(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SORT_RELEVANCE: _ClassVar[SortBy]
    SORT_PRICE_ASC: _ClassVar[SortBy]
    SORT_PRICE_DESC: _ClassVar[SortBy]
    SORT_RATING: _ClassVar[SortBy]
    SORT_NEWEST: _ClassVar[SortBy]

class PaymentStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    PAYMENT_STATUS_UNSPECIFIED: _ClassVar[PaymentStatus]
    PAYMENT_SUCCEEDED: _ClassVar[PaymentStatus]
    PAYMENT_DECLINED: _ClassVar[PaymentStatus]
    PAYMENT_REFUNDED: _ClassVar[PaymentStatus]

class OrderStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ORDER_STATUS_UNSPECIFIED: _ClassVar[OrderStatus]
    PENDING: _ClassVar[OrderStatus]
    CONFIRMED: _ClassVar[OrderStatus]
    PACKED: _ClassVar[OrderStatus]
    SHIPPED: _ClassVar[OrderStatus]
    OUT_FOR_DELIVERY: _ClassVar[OrderStatus]
    DELIVERED: _ClassVar[OrderStatus]
    CANCELLED: _ClassVar[OrderStatus]
    FAILED: _ClassVar[OrderStatus]

class CheckoutStep(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    CHECKOUT_STEP_UNSPECIFIED: _ClassVar[CheckoutStep]
    VALIDATING: _ClassVar[CheckoutStep]
    RESERVING: _ClassVar[CheckoutStep]
    CHARGING: _ClassVar[CheckoutStep]
    CONFIRMING: _ClassVar[CheckoutStep]
    COMPENSATING: _ClassVar[CheckoutStep]
    FINISHED: _ClassVar[CheckoutStep]

class StepState(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    STEP_STATE_UNSPECIFIED: _ClassVar[StepState]
    STEP_STARTED: _ClassVar[StepState]
    STEP_SUCCEEDED: _ClassVar[StepState]
    STEP_FAILED: _ClassVar[StepState]
SORT_RELEVANCE: SortBy
SORT_PRICE_ASC: SortBy
SORT_PRICE_DESC: SortBy
SORT_RATING: SortBy
SORT_NEWEST: SortBy
PAYMENT_STATUS_UNSPECIFIED: PaymentStatus
PAYMENT_SUCCEEDED: PaymentStatus
PAYMENT_DECLINED: PaymentStatus
PAYMENT_REFUNDED: PaymentStatus
ORDER_STATUS_UNSPECIFIED: OrderStatus
PENDING: OrderStatus
CONFIRMED: OrderStatus
PACKED: OrderStatus
SHIPPED: OrderStatus
OUT_FOR_DELIVERY: OrderStatus
DELIVERED: OrderStatus
CANCELLED: OrderStatus
FAILED: OrderStatus
CHECKOUT_STEP_UNSPECIFIED: CheckoutStep
VALIDATING: CheckoutStep
RESERVING: CheckoutStep
CHARGING: CheckoutStep
CONFIRMING: CheckoutStep
COMPENSATING: CheckoutStep
FINISHED: CheckoutStep
STEP_STATE_UNSPECIFIED: StepState
STEP_STARTED: StepState
STEP_SUCCEEDED: StepState
STEP_FAILED: StepState

class Sku(_message.Message):
    __slots__ = ("sku_id", "product_id", "size", "color")
    SKU_ID_FIELD_NUMBER: _ClassVar[int]
    PRODUCT_ID_FIELD_NUMBER: _ClassVar[int]
    SIZE_FIELD_NUMBER: _ClassVar[int]
    COLOR_FIELD_NUMBER: _ClassVar[int]
    sku_id: str
    product_id: str
    size: str
    color: str
    def __init__(self, sku_id: _Optional[str] = ..., product_id: _Optional[str] = ..., size: _Optional[str] = ..., color: _Optional[str] = ...) -> None: ...

class Product(_message.Message):
    __slots__ = ("product_id", "name", "category", "brand", "description", "price_cents", "rating", "rating_count", "colors", "sizes", "skus", "created_at")
    PRODUCT_ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    CATEGORY_FIELD_NUMBER: _ClassVar[int]
    BRAND_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    PRICE_CENTS_FIELD_NUMBER: _ClassVar[int]
    RATING_FIELD_NUMBER: _ClassVar[int]
    RATING_COUNT_FIELD_NUMBER: _ClassVar[int]
    COLORS_FIELD_NUMBER: _ClassVar[int]
    SIZES_FIELD_NUMBER: _ClassVar[int]
    SKUS_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    product_id: str
    name: str
    category: str
    brand: str
    description: str
    price_cents: int
    rating: float
    rating_count: int
    colors: _containers.RepeatedScalarFieldContainer[str]
    sizes: _containers.RepeatedScalarFieldContainer[str]
    skus: _containers.RepeatedCompositeFieldContainer[Sku]
    created_at: str
    def __init__(self, product_id: _Optional[str] = ..., name: _Optional[str] = ..., category: _Optional[str] = ..., brand: _Optional[str] = ..., description: _Optional[str] = ..., price_cents: _Optional[int] = ..., rating: _Optional[float] = ..., rating_count: _Optional[int] = ..., colors: _Optional[_Iterable[str]] = ..., sizes: _Optional[_Iterable[str]] = ..., skus: _Optional[_Iterable[_Union[Sku, _Mapping]]] = ..., created_at: _Optional[str] = ...) -> None: ...

class ListProductsRequest(_message.Message):
    __slots__ = ("query", "categories", "brands", "colors", "sizes", "min_price_cents", "max_price_cents", "sort", "page_size", "page_token")
    QUERY_FIELD_NUMBER: _ClassVar[int]
    CATEGORIES_FIELD_NUMBER: _ClassVar[int]
    BRANDS_FIELD_NUMBER: _ClassVar[int]
    COLORS_FIELD_NUMBER: _ClassVar[int]
    SIZES_FIELD_NUMBER: _ClassVar[int]
    MIN_PRICE_CENTS_FIELD_NUMBER: _ClassVar[int]
    MAX_PRICE_CENTS_FIELD_NUMBER: _ClassVar[int]
    SORT_FIELD_NUMBER: _ClassVar[int]
    PAGE_SIZE_FIELD_NUMBER: _ClassVar[int]
    PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    query: str
    categories: _containers.RepeatedScalarFieldContainer[str]
    brands: _containers.RepeatedScalarFieldContainer[str]
    colors: _containers.RepeatedScalarFieldContainer[str]
    sizes: _containers.RepeatedScalarFieldContainer[str]
    min_price_cents: int
    max_price_cents: int
    sort: SortBy
    page_size: int
    page_token: str
    def __init__(self, query: _Optional[str] = ..., categories: _Optional[_Iterable[str]] = ..., brands: _Optional[_Iterable[str]] = ..., colors: _Optional[_Iterable[str]] = ..., sizes: _Optional[_Iterable[str]] = ..., min_price_cents: _Optional[int] = ..., max_price_cents: _Optional[int] = ..., sort: _Optional[_Union[SortBy, str]] = ..., page_size: _Optional[int] = ..., page_token: _Optional[str] = ...) -> None: ...

class FacetValue(_message.Message):
    __slots__ = ("value", "count")
    VALUE_FIELD_NUMBER: _ClassVar[int]
    COUNT_FIELD_NUMBER: _ClassVar[int]
    value: str
    count: int
    def __init__(self, value: _Optional[str] = ..., count: _Optional[int] = ...) -> None: ...

class Facet(_message.Message):
    __slots__ = ("field", "values")
    FIELD_FIELD_NUMBER: _ClassVar[int]
    VALUES_FIELD_NUMBER: _ClassVar[int]
    field: str
    values: _containers.RepeatedCompositeFieldContainer[FacetValue]
    def __init__(self, field: _Optional[str] = ..., values: _Optional[_Iterable[_Union[FacetValue, _Mapping]]] = ...) -> None: ...

class ListProductsResponse(_message.Message):
    __slots__ = ("products", "total", "next_page_token", "facets", "price_min_cents", "price_max_cents", "fuzzy")
    PRODUCTS_FIELD_NUMBER: _ClassVar[int]
    TOTAL_FIELD_NUMBER: _ClassVar[int]
    NEXT_PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    FACETS_FIELD_NUMBER: _ClassVar[int]
    PRICE_MIN_CENTS_FIELD_NUMBER: _ClassVar[int]
    PRICE_MAX_CENTS_FIELD_NUMBER: _ClassVar[int]
    FUZZY_FIELD_NUMBER: _ClassVar[int]
    products: _containers.RepeatedCompositeFieldContainer[Product]
    total: int
    next_page_token: str
    facets: _containers.RepeatedCompositeFieldContainer[Facet]
    price_min_cents: int
    price_max_cents: int
    fuzzy: bool
    def __init__(self, products: _Optional[_Iterable[_Union[Product, _Mapping]]] = ..., total: _Optional[int] = ..., next_page_token: _Optional[str] = ..., facets: _Optional[_Iterable[_Union[Facet, _Mapping]]] = ..., price_min_cents: _Optional[int] = ..., price_max_cents: _Optional[int] = ..., fuzzy: _Optional[bool] = ...) -> None: ...

class GetProductRequest(_message.Message):
    __slots__ = ("product_id",)
    PRODUCT_ID_FIELD_NUMBER: _ClassVar[int]
    product_id: str
    def __init__(self, product_id: _Optional[str] = ...) -> None: ...

class GetProductsRequest(_message.Message):
    __slots__ = ("product_ids",)
    PRODUCT_IDS_FIELD_NUMBER: _ClassVar[int]
    product_ids: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, product_ids: _Optional[_Iterable[str]] = ...) -> None: ...

class GetProductsResponse(_message.Message):
    __slots__ = ("products",)
    PRODUCTS_FIELD_NUMBER: _ClassVar[int]
    products: _containers.RepeatedCompositeFieldContainer[Product]
    def __init__(self, products: _Optional[_Iterable[_Union[Product, _Mapping]]] = ...) -> None: ...

class GetSkusRequest(_message.Message):
    __slots__ = ("sku_ids",)
    SKU_IDS_FIELD_NUMBER: _ClassVar[int]
    sku_ids: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, sku_ids: _Optional[_Iterable[str]] = ...) -> None: ...

class GetSkusResponse(_message.Message):
    __slots__ = ("items",)
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    items: _containers.RepeatedCompositeFieldContainer[SkuWithProduct]
    def __init__(self, items: _Optional[_Iterable[_Union[SkuWithProduct, _Mapping]]] = ...) -> None: ...

class SkuWithProduct(_message.Message):
    __slots__ = ("sku", "name", "price_cents", "category")
    SKU_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    PRICE_CENTS_FIELD_NUMBER: _ClassVar[int]
    CATEGORY_FIELD_NUMBER: _ClassVar[int]
    sku: Sku
    name: str
    price_cents: int
    category: str
    def __init__(self, sku: _Optional[_Union[Sku, _Mapping]] = ..., name: _Optional[str] = ..., price_cents: _Optional[int] = ..., category: _Optional[str] = ...) -> None: ...

class StockLevel(_message.Message):
    __slots__ = ("sku_id", "product_id", "on_hand", "reserved", "available")
    SKU_ID_FIELD_NUMBER: _ClassVar[int]
    PRODUCT_ID_FIELD_NUMBER: _ClassVar[int]
    ON_HAND_FIELD_NUMBER: _ClassVar[int]
    RESERVED_FIELD_NUMBER: _ClassVar[int]
    AVAILABLE_FIELD_NUMBER: _ClassVar[int]
    sku_id: str
    product_id: str
    on_hand: int
    reserved: int
    available: int
    def __init__(self, sku_id: _Optional[str] = ..., product_id: _Optional[str] = ..., on_hand: _Optional[int] = ..., reserved: _Optional[int] = ..., available: _Optional[int] = ...) -> None: ...

class GetStockRequest(_message.Message):
    __slots__ = ("sku_ids", "product_ids")
    SKU_IDS_FIELD_NUMBER: _ClassVar[int]
    PRODUCT_IDS_FIELD_NUMBER: _ClassVar[int]
    sku_ids: _containers.RepeatedScalarFieldContainer[str]
    product_ids: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, sku_ids: _Optional[_Iterable[str]] = ..., product_ids: _Optional[_Iterable[str]] = ...) -> None: ...

class GetStockResponse(_message.Message):
    __slots__ = ("levels",)
    LEVELS_FIELD_NUMBER: _ClassVar[int]
    levels: _containers.RepeatedCompositeFieldContainer[StockLevel]
    def __init__(self, levels: _Optional[_Iterable[_Union[StockLevel, _Mapping]]] = ...) -> None: ...

class StockLine(_message.Message):
    __slots__ = ("sku_id", "quantity")
    SKU_ID_FIELD_NUMBER: _ClassVar[int]
    QUANTITY_FIELD_NUMBER: _ClassVar[int]
    sku_id: str
    quantity: int
    def __init__(self, sku_id: _Optional[str] = ..., quantity: _Optional[int] = ...) -> None: ...

class Shortage(_message.Message):
    __slots__ = ("sku_id", "requested", "available")
    SKU_ID_FIELD_NUMBER: _ClassVar[int]
    REQUESTED_FIELD_NUMBER: _ClassVar[int]
    AVAILABLE_FIELD_NUMBER: _ClassVar[int]
    sku_id: str
    requested: int
    available: int
    def __init__(self, sku_id: _Optional[str] = ..., requested: _Optional[int] = ..., available: _Optional[int] = ...) -> None: ...

class ReserveRequest(_message.Message):
    __slots__ = ("reservation_id", "lines", "ttl_seconds")
    RESERVATION_ID_FIELD_NUMBER: _ClassVar[int]
    LINES_FIELD_NUMBER: _ClassVar[int]
    TTL_SECONDS_FIELD_NUMBER: _ClassVar[int]
    reservation_id: str
    lines: _containers.RepeatedCompositeFieldContainer[StockLine]
    ttl_seconds: int
    def __init__(self, reservation_id: _Optional[str] = ..., lines: _Optional[_Iterable[_Union[StockLine, _Mapping]]] = ..., ttl_seconds: _Optional[int] = ...) -> None: ...

class ReserveResponse(_message.Message):
    __slots__ = ("ok", "shortages", "expires_at")
    OK_FIELD_NUMBER: _ClassVar[int]
    SHORTAGES_FIELD_NUMBER: _ClassVar[int]
    EXPIRES_AT_FIELD_NUMBER: _ClassVar[int]
    ok: bool
    shortages: _containers.RepeatedCompositeFieldContainer[Shortage]
    expires_at: str
    def __init__(self, ok: _Optional[bool] = ..., shortages: _Optional[_Iterable[_Union[Shortage, _Mapping]]] = ..., expires_at: _Optional[str] = ...) -> None: ...

class CommitRequest(_message.Message):
    __slots__ = ("reservation_id",)
    RESERVATION_ID_FIELD_NUMBER: _ClassVar[int]
    reservation_id: str
    def __init__(self, reservation_id: _Optional[str] = ...) -> None: ...

class CommitResponse(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ReleaseRequest(_message.Message):
    __slots__ = ("reservation_id", "reason")
    RESERVATION_ID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    reservation_id: str
    reason: str
    def __init__(self, reservation_id: _Optional[str] = ..., reason: _Optional[str] = ...) -> None: ...

class ReleaseResponse(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class RestockRequest(_message.Message):
    __slots__ = ("idempotency_key", "lines")
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    LINES_FIELD_NUMBER: _ClassVar[int]
    idempotency_key: str
    lines: _containers.RepeatedCompositeFieldContainer[StockLine]
    def __init__(self, idempotency_key: _Optional[str] = ..., lines: _Optional[_Iterable[_Union[StockLine, _Mapping]]] = ...) -> None: ...

class RestockResponse(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class User(_message.Message):
    __slots__ = ("user_id", "name", "email", "city")
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    EMAIL_FIELD_NUMBER: _ClassVar[int]
    CITY_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    name: str
    email: str
    city: str
    def __init__(self, user_id: _Optional[str] = ..., name: _Optional[str] = ..., email: _Optional[str] = ..., city: _Optional[str] = ...) -> None: ...

class ListUsersRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ListUsersResponse(_message.Message):
    __slots__ = ("users",)
    USERS_FIELD_NUMBER: _ClassVar[int]
    users: _containers.RepeatedCompositeFieldContainer[User]
    def __init__(self, users: _Optional[_Iterable[_Union[User, _Mapping]]] = ...) -> None: ...

class AuthenticateRequest(_message.Message):
    __slots__ = ("email", "password")
    EMAIL_FIELD_NUMBER: _ClassVar[int]
    PASSWORD_FIELD_NUMBER: _ClassVar[int]
    email: str
    password: str
    def __init__(self, email: _Optional[str] = ..., password: _Optional[str] = ...) -> None: ...

class CreateUserRequest(_message.Message):
    __slots__ = ("name", "email", "password", "city")
    NAME_FIELD_NUMBER: _ClassVar[int]
    EMAIL_FIELD_NUMBER: _ClassVar[int]
    PASSWORD_FIELD_NUMBER: _ClassVar[int]
    CITY_FIELD_NUMBER: _ClassVar[int]
    name: str
    email: str
    password: str
    city: str
    def __init__(self, name: _Optional[str] = ..., email: _Optional[str] = ..., password: _Optional[str] = ..., city: _Optional[str] = ...) -> None: ...

class GetUserRequest(_message.Message):
    __slots__ = ("user_id",)
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    def __init__(self, user_id: _Optional[str] = ...) -> None: ...

class CartLine(_message.Message):
    __slots__ = ("sku_id", "product_id", "quantity", "added_at")
    SKU_ID_FIELD_NUMBER: _ClassVar[int]
    PRODUCT_ID_FIELD_NUMBER: _ClassVar[int]
    QUANTITY_FIELD_NUMBER: _ClassVar[int]
    ADDED_AT_FIELD_NUMBER: _ClassVar[int]
    sku_id: str
    product_id: str
    quantity: int
    added_at: str
    def __init__(self, sku_id: _Optional[str] = ..., product_id: _Optional[str] = ..., quantity: _Optional[int] = ..., added_at: _Optional[str] = ...) -> None: ...

class Cart(_message.Message):
    __slots__ = ("user_id", "lines", "version", "updated_at")
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    LINES_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    user_id: str
    lines: _containers.RepeatedCompositeFieldContainer[CartLine]
    version: int
    updated_at: str
    def __init__(self, user_id: _Optional[str] = ..., lines: _Optional[_Iterable[_Union[CartLine, _Mapping]]] = ..., version: _Optional[int] = ..., updated_at: _Optional[str] = ...) -> None: ...

class GetCartRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class SetCartLineRequest(_message.Message):
    __slots__ = ("sku_id", "product_id", "quantity")
    SKU_ID_FIELD_NUMBER: _ClassVar[int]
    PRODUCT_ID_FIELD_NUMBER: _ClassVar[int]
    QUANTITY_FIELD_NUMBER: _ClassVar[int]
    sku_id: str
    product_id: str
    quantity: int
    def __init__(self, sku_id: _Optional[str] = ..., product_id: _Optional[str] = ..., quantity: _Optional[int] = ...) -> None: ...

class ClearCartRequest(_message.Message):
    __slots__ = ("if_version",)
    IF_VERSION_FIELD_NUMBER: _ClassVar[int]
    if_version: int
    def __init__(self, if_version: _Optional[int] = ...) -> None: ...

class ChargeRequest(_message.Message):
    __slots__ = ("idempotency_key", "order_id", "amount_cents", "payment_method")
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    AMOUNT_CENTS_FIELD_NUMBER: _ClassVar[int]
    PAYMENT_METHOD_FIELD_NUMBER: _ClassVar[int]
    idempotency_key: str
    order_id: str
    amount_cents: int
    payment_method: str
    def __init__(self, idempotency_key: _Optional[str] = ..., order_id: _Optional[str] = ..., amount_cents: _Optional[int] = ..., payment_method: _Optional[str] = ...) -> None: ...

class ChargeResponse(_message.Message):
    __slots__ = ("payment_id", "status", "decline_reason")
    PAYMENT_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    DECLINE_REASON_FIELD_NUMBER: _ClassVar[int]
    payment_id: str
    status: PaymentStatus
    decline_reason: str
    def __init__(self, payment_id: _Optional[str] = ..., status: _Optional[_Union[PaymentStatus, str]] = ..., decline_reason: _Optional[str] = ...) -> None: ...

class RefundRequest(_message.Message):
    __slots__ = ("idempotency_key", "payment_id", "amount_cents")
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    PAYMENT_ID_FIELD_NUMBER: _ClassVar[int]
    AMOUNT_CENTS_FIELD_NUMBER: _ClassVar[int]
    idempotency_key: str
    payment_id: str
    amount_cents: int
    def __init__(self, idempotency_key: _Optional[str] = ..., payment_id: _Optional[str] = ..., amount_cents: _Optional[int] = ...) -> None: ...

class RefundResponse(_message.Message):
    __slots__ = ("refund_id", "status")
    REFUND_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    refund_id: str
    status: PaymentStatus
    def __init__(self, refund_id: _Optional[str] = ..., status: _Optional[_Union[PaymentStatus, str]] = ...) -> None: ...

class LookupChargeRequest(_message.Message):
    __slots__ = ("idempotency_key",)
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    idempotency_key: str
    def __init__(self, idempotency_key: _Optional[str] = ...) -> None: ...

class OrderLine(_message.Message):
    __slots__ = ("sku_id", "product_id", "name", "size", "color", "quantity", "unit_price_cents", "category")
    SKU_ID_FIELD_NUMBER: _ClassVar[int]
    PRODUCT_ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    SIZE_FIELD_NUMBER: _ClassVar[int]
    COLOR_FIELD_NUMBER: _ClassVar[int]
    QUANTITY_FIELD_NUMBER: _ClassVar[int]
    UNIT_PRICE_CENTS_FIELD_NUMBER: _ClassVar[int]
    CATEGORY_FIELD_NUMBER: _ClassVar[int]
    sku_id: str
    product_id: str
    name: str
    size: str
    color: str
    quantity: int
    unit_price_cents: int
    category: str
    def __init__(self, sku_id: _Optional[str] = ..., product_id: _Optional[str] = ..., name: _Optional[str] = ..., size: _Optional[str] = ..., color: _Optional[str] = ..., quantity: _Optional[int] = ..., unit_price_cents: _Optional[int] = ..., category: _Optional[str] = ...) -> None: ...

class OrderEvent(_message.Message):
    __slots__ = ("event_id", "order_id", "status", "message", "timestamp")
    EVENT_ID_FIELD_NUMBER: _ClassVar[int]
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    event_id: int
    order_id: str
    status: OrderStatus
    message: str
    timestamp: str
    def __init__(self, event_id: _Optional[int] = ..., order_id: _Optional[str] = ..., status: _Optional[_Union[OrderStatus, str]] = ..., message: _Optional[str] = ..., timestamp: _Optional[str] = ...) -> None: ...

class Order(_message.Message):
    __slots__ = ("order_id", "user_id", "status", "lines", "subtotal_cents", "shipping_cents", "total_cents", "payment_id", "failure_reason", "created_at", "updated_at", "history")
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    LINES_FIELD_NUMBER: _ClassVar[int]
    SUBTOTAL_CENTS_FIELD_NUMBER: _ClassVar[int]
    SHIPPING_CENTS_FIELD_NUMBER: _ClassVar[int]
    TOTAL_CENTS_FIELD_NUMBER: _ClassVar[int]
    PAYMENT_ID_FIELD_NUMBER: _ClassVar[int]
    FAILURE_REASON_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    HISTORY_FIELD_NUMBER: _ClassVar[int]
    order_id: str
    user_id: str
    status: OrderStatus
    lines: _containers.RepeatedCompositeFieldContainer[OrderLine]
    subtotal_cents: int
    shipping_cents: int
    total_cents: int
    payment_id: str
    failure_reason: str
    created_at: str
    updated_at: str
    history: _containers.RepeatedCompositeFieldContainer[OrderEvent]
    def __init__(self, order_id: _Optional[str] = ..., user_id: _Optional[str] = ..., status: _Optional[_Union[OrderStatus, str]] = ..., lines: _Optional[_Iterable[_Union[OrderLine, _Mapping]]] = ..., subtotal_cents: _Optional[int] = ..., shipping_cents: _Optional[int] = ..., total_cents: _Optional[int] = ..., payment_id: _Optional[str] = ..., failure_reason: _Optional[str] = ..., created_at: _Optional[str] = ..., updated_at: _Optional[str] = ..., history: _Optional[_Iterable[_Union[OrderEvent, _Mapping]]] = ...) -> None: ...

class CheckoutLine(_message.Message):
    __slots__ = ("sku_id", "quantity")
    SKU_ID_FIELD_NUMBER: _ClassVar[int]
    QUANTITY_FIELD_NUMBER: _ClassVar[int]
    sku_id: str
    quantity: int
    def __init__(self, sku_id: _Optional[str] = ..., quantity: _Optional[int] = ...) -> None: ...

class CheckoutRequest(_message.Message):
    __slots__ = ("idempotency_key", "lines", "payment_method")
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    LINES_FIELD_NUMBER: _ClassVar[int]
    PAYMENT_METHOD_FIELD_NUMBER: _ClassVar[int]
    idempotency_key: str
    lines: _containers.RepeatedCompositeFieldContainer[CheckoutLine]
    payment_method: str
    def __init__(self, idempotency_key: _Optional[str] = ..., lines: _Optional[_Iterable[_Union[CheckoutLine, _Mapping]]] = ..., payment_method: _Optional[str] = ...) -> None: ...

class CheckoutEvent(_message.Message):
    __slots__ = ("order_id", "step", "state", "message", "shortages", "order", "timestamp")
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    STEP_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    SHORTAGES_FIELD_NUMBER: _ClassVar[int]
    ORDER_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    order_id: str
    step: CheckoutStep
    state: StepState
    message: str
    shortages: _containers.RepeatedCompositeFieldContainer[Shortage]
    order: Order
    timestamp: str
    def __init__(self, order_id: _Optional[str] = ..., step: _Optional[_Union[CheckoutStep, str]] = ..., state: _Optional[_Union[StepState, str]] = ..., message: _Optional[str] = ..., shortages: _Optional[_Iterable[_Union[Shortage, _Mapping]]] = ..., order: _Optional[_Union[Order, _Mapping]] = ..., timestamp: _Optional[str] = ...) -> None: ...

class ListOrdersRequest(_message.Message):
    __slots__ = ("page_size", "page_token")
    PAGE_SIZE_FIELD_NUMBER: _ClassVar[int]
    PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    page_size: int
    page_token: str
    def __init__(self, page_size: _Optional[int] = ..., page_token: _Optional[str] = ...) -> None: ...

class ListOrdersResponse(_message.Message):
    __slots__ = ("orders", "next_page_token")
    ORDERS_FIELD_NUMBER: _ClassVar[int]
    NEXT_PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    orders: _containers.RepeatedCompositeFieldContainer[Order]
    next_page_token: str
    def __init__(self, orders: _Optional[_Iterable[_Union[Order, _Mapping]]] = ..., next_page_token: _Optional[str] = ...) -> None: ...

class GetOrderRequest(_message.Message):
    __slots__ = ("order_id",)
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    order_id: str
    def __init__(self, order_id: _Optional[str] = ...) -> None: ...

class CancelOrderRequest(_message.Message):
    __slots__ = ("order_id", "idempotency_key")
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    order_id: str
    idempotency_key: str
    def __init__(self, order_id: _Optional[str] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class WatchOrdersRequest(_message.Message):
    __slots__ = ("order_id", "after_event_id")
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    AFTER_EVENT_ID_FIELD_NUMBER: _ClassVar[int]
    order_id: str
    after_event_id: int
    def __init__(self, order_id: _Optional[str] = ..., after_event_id: _Optional[int] = ...) -> None: ...

class Conversation(_message.Message):
    __slots__ = ("conversation_id", "title", "summary", "created_at", "updated_at")
    CONVERSATION_ID_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    SUMMARY_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    conversation_id: str
    title: str
    summary: str
    created_at: str
    updated_at: str
    def __init__(self, conversation_id: _Optional[str] = ..., title: _Optional[str] = ..., summary: _Optional[str] = ..., created_at: _Optional[str] = ..., updated_at: _Optional[str] = ...) -> None: ...

class ChatMessage(_message.Message):
    __slots__ = ("message_id", "conversation_id", "role", "text", "cards_json", "created_at")
    MESSAGE_ID_FIELD_NUMBER: _ClassVar[int]
    CONVERSATION_ID_FIELD_NUMBER: _ClassVar[int]
    ROLE_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_NUMBER: _ClassVar[int]
    CARDS_JSON_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    message_id: int
    conversation_id: str
    role: str
    text: str
    cards_json: str
    created_at: str
    def __init__(self, message_id: _Optional[int] = ..., conversation_id: _Optional[str] = ..., role: _Optional[str] = ..., text: _Optional[str] = ..., cards_json: _Optional[str] = ..., created_at: _Optional[str] = ...) -> None: ...

class Fact(_message.Message):
    __slots__ = ("fact_id", "kind", "key", "value", "category", "expires_at", "score")
    FACT_ID_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    KEY_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    CATEGORY_FIELD_NUMBER: _ClassVar[int]
    EXPIRES_AT_FIELD_NUMBER: _ClassVar[int]
    SCORE_FIELD_NUMBER: _ClassVar[int]
    fact_id: str
    kind: str
    key: str
    value: str
    category: str
    expires_at: str
    score: float
    def __init__(self, fact_id: _Optional[str] = ..., kind: _Optional[str] = ..., key: _Optional[str] = ..., value: _Optional[str] = ..., category: _Optional[str] = ..., expires_at: _Optional[str] = ..., score: _Optional[float] = ...) -> None: ...

class FactChange(_message.Message):
    __slots__ = ("op", "kind", "key", "value", "category", "embedding")
    OP_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    KEY_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    CATEGORY_FIELD_NUMBER: _ClassVar[int]
    EMBEDDING_FIELD_NUMBER: _ClassVar[int]
    op: str
    kind: str
    key: str
    value: str
    category: str
    embedding: _containers.RepeatedScalarFieldContainer[float]
    def __init__(self, op: _Optional[str] = ..., kind: _Optional[str] = ..., key: _Optional[str] = ..., value: _Optional[str] = ..., category: _Optional[str] = ..., embedding: _Optional[_Iterable[float]] = ...) -> None: ...

class CreateConversationRequest(_message.Message):
    __slots__ = ("title",)
    TITLE_FIELD_NUMBER: _ClassVar[int]
    title: str
    def __init__(self, title: _Optional[str] = ...) -> None: ...

class ListConversationsRequest(_message.Message):
    __slots__ = ("page_size",)
    PAGE_SIZE_FIELD_NUMBER: _ClassVar[int]
    page_size: int
    def __init__(self, page_size: _Optional[int] = ...) -> None: ...

class ListConversationsResponse(_message.Message):
    __slots__ = ("conversations",)
    CONVERSATIONS_FIELD_NUMBER: _ClassVar[int]
    conversations: _containers.RepeatedCompositeFieldContainer[Conversation]
    def __init__(self, conversations: _Optional[_Iterable[_Union[Conversation, _Mapping]]] = ...) -> None: ...

class GetConversationRequest(_message.Message):
    __slots__ = ("conversation_id",)
    CONVERSATION_ID_FIELD_NUMBER: _ClassVar[int]
    conversation_id: str
    def __init__(self, conversation_id: _Optional[str] = ...) -> None: ...

class RenameConversationRequest(_message.Message):
    __slots__ = ("conversation_id", "title")
    CONVERSATION_ID_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    conversation_id: str
    title: str
    def __init__(self, conversation_id: _Optional[str] = ..., title: _Optional[str] = ...) -> None: ...

class DeleteConversationRequest(_message.Message):
    __slots__ = ("conversation_id",)
    CONVERSATION_ID_FIELD_NUMBER: _ClassVar[int]
    conversation_id: str
    def __init__(self, conversation_id: _Optional[str] = ...) -> None: ...

class DeleteConversationResponse(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class AppendMessageRequest(_message.Message):
    __slots__ = ("conversation_id", "role", "text", "cards_json")
    CONVERSATION_ID_FIELD_NUMBER: _ClassVar[int]
    ROLE_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_NUMBER: _ClassVar[int]
    CARDS_JSON_FIELD_NUMBER: _ClassVar[int]
    conversation_id: str
    role: str
    text: str
    cards_json: str
    def __init__(self, conversation_id: _Optional[str] = ..., role: _Optional[str] = ..., text: _Optional[str] = ..., cards_json: _Optional[str] = ...) -> None: ...

class ListMessagesRequest(_message.Message):
    __slots__ = ("conversation_id", "limit")
    CONVERSATION_ID_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    conversation_id: str
    limit: int
    def __init__(self, conversation_id: _Optional[str] = ..., limit: _Optional[int] = ...) -> None: ...

class ListMessagesResponse(_message.Message):
    __slots__ = ("messages",)
    MESSAGES_FIELD_NUMBER: _ClassVar[int]
    messages: _containers.RepeatedCompositeFieldContainer[ChatMessage]
    def __init__(self, messages: _Optional[_Iterable[_Union[ChatMessage, _Mapping]]] = ...) -> None: ...

class SetSummaryRequest(_message.Message):
    __slots__ = ("conversation_id", "summary")
    CONVERSATION_ID_FIELD_NUMBER: _ClassVar[int]
    SUMMARY_FIELD_NUMBER: _ClassVar[int]
    conversation_id: str
    summary: str
    def __init__(self, conversation_id: _Optional[str] = ..., summary: _Optional[str] = ...) -> None: ...

class ApplyFactsRequest(_message.Message):
    __slots__ = ("changes",)
    CHANGES_FIELD_NUMBER: _ClassVar[int]
    changes: _containers.RepeatedCompositeFieldContainer[FactChange]
    def __init__(self, changes: _Optional[_Iterable[_Union[FactChange, _Mapping]]] = ...) -> None: ...

class SearchFactsRequest(_message.Message):
    __slots__ = ("query_text", "query_embedding", "limit", "min_score")
    QUERY_TEXT_FIELD_NUMBER: _ClassVar[int]
    QUERY_EMBEDDING_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    MIN_SCORE_FIELD_NUMBER: _ClassVar[int]
    query_text: str
    query_embedding: _containers.RepeatedScalarFieldContainer[float]
    limit: int
    min_score: float
    def __init__(self, query_text: _Optional[str] = ..., query_embedding: _Optional[_Iterable[float]] = ..., limit: _Optional[int] = ..., min_score: _Optional[float] = ...) -> None: ...

class ListFactsRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ListFactsResponse(_message.Message):
    __slots__ = ("facts",)
    FACTS_FIELD_NUMBER: _ClassVar[int]
    facts: _containers.RepeatedCompositeFieldContainer[Fact]
    def __init__(self, facts: _Optional[_Iterable[_Union[Fact, _Mapping]]] = ...) -> None: ...

class ClearTemporalFactsRequest(_message.Message):
    __slots__ = ("categories",)
    CATEGORIES_FIELD_NUMBER: _ClassVar[int]
    categories: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, categories: _Optional[_Iterable[str]] = ...) -> None: ...

class ClearTemporalFactsResponse(_message.Message):
    __slots__ = ("removed",)
    REMOVED_FIELD_NUMBER: _ClassVar[int]
    removed: int
    def __init__(self, removed: _Optional[int] = ...) -> None: ...
