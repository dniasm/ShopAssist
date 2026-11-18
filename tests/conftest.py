"""Shared fixtures. Tests use small fixture data written to tmp_path, never the real seed files."""
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

PRODUCTS = [
    {"product_id": "P-1001", "name": "Wireless Headphones", "category": "headphones", "color": "black", "price": 89.99,
     "description": "Over-ear wireless headphones"},
    {"product_id": "P-1002", "name": "Budget Earbuds", "category": "headphones", "color": "white", "price": 39.99,
     "description": "Cheap in-ear earbuds"},
    {"product_id": "P-1003", "name": "Studio Headphones", "category": "headphones", "color": "black", "price": 199.0,
     "description": "Pro studio headphones"},
    {"product_id": "P-1004", "name": "Classic Hoodie", "category": "hoodies", "color": "blue", "price": 49.5,
     "description": "Soft blue hoodie"},
    {"product_id": "P-1005", "name": "Trail Backpack", "category": "backpacks", "color": "green", "price": 60.0,
     "description": "20L backpack"},
]
INVENTORY = [
    {"product_id": "P-1001", "size": "ONE", "quantity": 7},
    {"product_id": "P-1002", "size": "ONE", "quantity": 0},
    {"product_id": "P-1003", "size": "ONE", "quantity": 3},
    {"product_id": "P-1004", "size": "S", "quantity": 5},
    {"product_id": "P-1004", "size": "M", "quantity": 4},
    {"product_id": "P-1004", "size": "L", "quantity": 0},
    {"product_id": "P-1004", "size": "XL", "quantity": 2},
    {"product_id": "P-1005", "size": "ONE", "quantity": 9},
]


def _order(order_id, status, items):
    return {"order_id": order_id, "customer_name": "Test Customer", "status": status, "items": items,
            "updated_at": "2026-01-01T00:00:00Z"}


ORDERS = [
    _order("1042", "SHIPPED", [{"product_id": "P-1004", "size": "M", "quantity": 1, "unit_price": 49.5},
                               {"product_id": "P-1005", "size": "ONE", "quantity": 2, "unit_price": 60.0}]),
    _order("1043", "PLACED", [{"product_id": "P-1001", "size": "ONE", "quantity": 1, "unit_price": 89.99}]),
    _order("1044", "DELIVERED", [{"product_id": "P-1002", "size": "ONE", "quantity": 1, "unit_price": 39.99}]),
    _order("1045", "CANCELLED", [{"product_id": "P-1003", "size": "ONE", "quantity": 1, "unit_price": 199.0}]),
]


@pytest.fixture
def seed_dir(tmp_path):
    d = tmp_path / "seed"
    d.mkdir()
    for name, records in (("products", PRODUCTS), ("inventory", INVENTORY), ("orders", ORDERS)):
        (d / f"{name}.json").write_text(json.dumps(records), encoding="utf-8")
    return d


@pytest.fixture
def grpc_services(tmp_path, seed_dir):
    """Real product, inventory and order gRPC servers on free localhost ports, each with its own temp DB."""
    from services.inventory.server import create_server as create_inventory
    from services.order.server import create_server as create_order
    from services.product.server import create_server as create_product

    product, p_port = create_product(tmp_path / "products.db", seed_dir / "products.json")
    inventory, i_port = create_inventory(tmp_path / "inventory.db", seed_dir / "inventory.json")
    order, o_port = create_order(tmp_path / "orders.db", seed_dir / "orders.json", step_seconds=0.05)
    servers = [product, inventory, order]
    for s in servers:
        s.start()
    yield {"product": f"127.0.0.1:{p_port}", "inventory": f"127.0.0.1:{i_port}", "order": f"127.0.0.1:{o_port}"}
    for s in servers:
        s.stop(0).wait()
