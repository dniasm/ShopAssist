"""Answer templates are pure functions of (entities, data)."""
from orchestrator.composer import HELP, compose

ORDER = {"order_id": "1042", "customer_name": "Ann", "status": "SHIPPED", "total": 169.5,
         "updated_at": "2026-01-01T00:00:00Z",
         "items": [{"product_id": "P-1004", "name": "Classic Hoodie", "size": "M", "quantity": 1, "unit_price": 49.5},
                   {"product_id": "P-1005", "name": "Trail Backpack", "size": "ONE", "quantity": 2, "unit_price": 60.0}]}
HOODIE = {"product_id": "P-1004", "name": "Classic Hoodie", "category": "hoodies", "color": "blue", "price": 49.5,
          "description": "Soft blue hoodie"}


def test_order_status():
    text = compose("order_status", {"order_id": "1042"}, {"order": ORDER})
    assert "Order 1042" in text and "shipped" in text
    assert "1 x Classic Hoodie (size M)" in text and "2 x Trail Backpack at $60.00" in text and "$169.50" in text


def test_order_not_found_is_friendly():
    assert "couldn't find order 9999" in compose("order_status", {"order_id": "9999"}, {})
    assert "order number" in compose("order_status", {}, {})


def test_track_order():
    ups = [{"status": s, "message": s} for s in ("PLACED", "PACKED", "DELIVERED")]
    text = compose("track_order", {"order_id": "1043"}, {"updates": ups})
    assert "delivered" in text and "PLACED -> PACKED -> DELIVERED" in text
    assert "was cancelled" in compose("track_order", {"order_id": "1045"}, {"updates": [{"status": "CANCELLED", "message": "x"}]})
    assert "couldn't find order 9" in compose("track_order", {"order_id": "9"}, {"updates": []})


def test_product_search():
    text = compose("product_search", {"max_price": 100.0}, {"products": [HOODIE]})
    assert "I found 1 product up to $100.00" in text and "Classic Hoodie (P-1004), blue, $49.50" in text
    assert "couldn't find any products" in compose("product_search", {}, {"products": []})


def test_stock_check_variants():
    stock = [{"size": "M", "quantity": 4}]
    base = {"results": [{"product": HOODIE, "stock": stock}]}
    assert "size M: 4 in stock" in compose("stock_check", {"size": "M"}, base)
    out = {"results": [{"product": HOODIE, "stock": [{"size": "L", "quantity": 0}]}]}
    assert "size L: out of stock" in compose("stock_check", {"size": "L"}, out)
    assert "size XL is not carried" in compose("stock_check", {"size": "XL"}, {"results": [{"product": HOODIE, "stock": []}]})
    assert "couldn't find a product" in compose("stock_check", {}, {"results": []})


def test_product_info():
    data = {"product": HOODIE, "stock": [{"size": "S", "quantity": 5}, {"size": "L", "quantity": 0}]}
    text = compose("product_info", {"product_id": "P-1004"}, data)
    assert "Soft blue hoodie" in text and "Availability: S: 5, L: out of stock." in text
    one = compose("product_info", {"product_id": "P-1001"}, {"product": HOODIE, "stock": [{"size": "ONE", "quantity": 7}]})
    assert "Availability: 7 in stock." in one
    assert "couldn't find product P-9999" in compose("product_info", {"product_id": "P-9999"}, {})


def test_unknown_lists_capabilities():
    assert HELP in compose("unknown", {}, {})
