from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class OrderStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ORDER_STATUS_UNSPECIFIED: _ClassVar[OrderStatus]
    PLACED: _ClassVar[OrderStatus]
    PACKED: _ClassVar[OrderStatus]
    SHIPPED: _ClassVar[OrderStatus]
    OUT_FOR_DELIVERY: _ClassVar[OrderStatus]
    DELIVERED: _ClassVar[OrderStatus]
    CANCELLED: _ClassVar[OrderStatus]
ORDER_STATUS_UNSPECIFIED: OrderStatus
PLACED: OrderStatus
PACKED: OrderStatus
SHIPPED: OrderStatus
OUT_FOR_DELIVERY: OrderStatus
DELIVERED: OrderStatus
CANCELLED: OrderStatus

class Product(_message.Message):
    __slots__ = ("product_id", "name", "category", "color", "price", "description")
    PRODUCT_ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    CATEGORY_FIELD_NUMBER: _ClassVar[int]
    COLOR_FIELD_NUMBER: _ClassVar[int]
    PRICE_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    product_id: str
    name: str
    category: str
    color: str
    price: float
    description: str
    def __init__(self, product_id: _Optional[str] = ..., name: _Optional[str] = ..., category: _Optional[str] = ..., color: _Optional[str] = ..., price: _Optional[float] = ..., description: _Optional[str] = ...) -> None: ...

class SearchProductsRequest(_message.Message):
    __slots__ = ("text", "category", "max_price", "limit")
    TEXT_FIELD_NUMBER: _ClassVar[int]
    CATEGORY_FIELD_NUMBER: _ClassVar[int]
    MAX_PRICE_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    text: str
    category: str
    max_price: float
    limit: int
    def __init__(self, text: _Optional[str] = ..., category: _Optional[str] = ..., max_price: _Optional[float] = ..., limit: _Optional[int] = ...) -> None: ...

class SearchProductsResponse(_message.Message):
    __slots__ = ("products",)
    PRODUCTS_FIELD_NUMBER: _ClassVar[int]
    products: _containers.RepeatedCompositeFieldContainer[Product]
    def __init__(self, products: _Optional[_Iterable[_Union[Product, _Mapping]]] = ...) -> None: ...

class GetProductRequest(_message.Message):
    __slots__ = ("product_id",)
    PRODUCT_ID_FIELD_NUMBER: _ClassVar[int]
    product_id: str
    def __init__(self, product_id: _Optional[str] = ...) -> None: ...

class StockRequest(_message.Message):
    __slots__ = ("product_id", "size")
    PRODUCT_ID_FIELD_NUMBER: _ClassVar[int]
    SIZE_FIELD_NUMBER: _ClassVar[int]
    product_id: str
    size: str
    def __init__(self, product_id: _Optional[str] = ..., size: _Optional[str] = ...) -> None: ...

class StockLevel(_message.Message):
    __slots__ = ("product_id", "size", "quantity")
    PRODUCT_ID_FIELD_NUMBER: _ClassVar[int]
    SIZE_FIELD_NUMBER: _ClassVar[int]
    QUANTITY_FIELD_NUMBER: _ClassVar[int]
    product_id: str
    size: str
    quantity: int
    def __init__(self, product_id: _Optional[str] = ..., size: _Optional[str] = ..., quantity: _Optional[int] = ...) -> None: ...

class StockResponse(_message.Message):
    __slots__ = ("levels",)
    LEVELS_FIELD_NUMBER: _ClassVar[int]
    levels: _containers.RepeatedCompositeFieldContainer[StockLevel]
    def __init__(self, levels: _Optional[_Iterable[_Union[StockLevel, _Mapping]]] = ...) -> None: ...

class OrderItem(_message.Message):
    __slots__ = ("product_id", "size", "quantity", "unit_price")
    PRODUCT_ID_FIELD_NUMBER: _ClassVar[int]
    SIZE_FIELD_NUMBER: _ClassVar[int]
    QUANTITY_FIELD_NUMBER: _ClassVar[int]
    UNIT_PRICE_FIELD_NUMBER: _ClassVar[int]
    product_id: str
    size: str
    quantity: int
    unit_price: float
    def __init__(self, product_id: _Optional[str] = ..., size: _Optional[str] = ..., quantity: _Optional[int] = ..., unit_price: _Optional[float] = ...) -> None: ...

class Order(_message.Message):
    __slots__ = ("order_id", "customer_name", "status", "items", "total", "updated_at")
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    CUSTOMER_NAME_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    TOTAL_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    order_id: str
    customer_name: str
    status: OrderStatus
    items: _containers.RepeatedCompositeFieldContainer[OrderItem]
    total: float
    updated_at: str
    def __init__(self, order_id: _Optional[str] = ..., customer_name: _Optional[str] = ..., status: _Optional[_Union[OrderStatus, str]] = ..., items: _Optional[_Iterable[_Union[OrderItem, _Mapping]]] = ..., total: _Optional[float] = ..., updated_at: _Optional[str] = ...) -> None: ...

class GetOrderRequest(_message.Message):
    __slots__ = ("order_id",)
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    order_id: str
    def __init__(self, order_id: _Optional[str] = ...) -> None: ...

class TrackOrderRequest(_message.Message):
    __slots__ = ("order_id",)
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    order_id: str
    def __init__(self, order_id: _Optional[str] = ...) -> None: ...

class OrderStatusUpdate(_message.Message):
    __slots__ = ("order_id", "status", "message", "timestamp")
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    order_id: str
    status: OrderStatus
    message: str
    timestamp: str
    def __init__(self, order_id: _Optional[str] = ..., status: _Optional[_Union[OrderStatus, str]] = ..., message: _Optional[str] = ..., timestamp: _Optional[str] = ...) -> None: ...
