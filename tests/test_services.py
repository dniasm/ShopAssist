"""Each gRPC service against a temp DB, through a real channel."""
import sqlite3

import grpc
import pytest

from shopassist_proto import shopassist_pb2 as pb
from shopassist_proto import shopassist_pb2_grpc as pb_grpc


def _channel(addr):
    return grpc.insecure_channel(addr)


# ---- Product ----

def test_product_get_and_not_found(grpc_services):
    stub = pb_grpc.ProductServiceStub(_channel(grpc_services["product"]))
    p = stub.GetProduct(pb.GetProductRequest(product_id="P-1004"))
    assert (p.name, p.category, p.color, p.price) == ("Classic Hoodie", "hoodies", "blue", 49.5)
    with pytest.raises(grpc.RpcError) as e:
        stub.GetProduct(pb.GetProductRequest(product_id="P-9999"))
    assert e.value.code() == grpc.StatusCode.NOT_FOUND


def test_product_search_filters(grpc_services):
    stub = pb_grpc.ProductServiceStub(_channel(grpc_services["product"]))

    def search(**kw):
        return [p.product_id for p in stub.SearchProducts(pb.SearchProductsRequest(**kw)).products]

    assert search(category="headphones", max_price=100) == ["P-1002", "P-1001"]  # cheapest first, studio excluded
    assert search(text="blue", category="hoodies") == ["P-1004"]
    assert search(text="BLUE") == ["P-1004"]  # case-insensitive, matches color/description
    assert search(text="blue black") == []  # every word must match
    assert search(category="shoes") == []
    assert len(search()) == 5
    assert len(search(limit=2)) == 2


# ---- Inventory ----

def test_inventory_levels(grpc_services):
    stub = pb_grpc.InventoryServiceStub(_channel(grpc_services["inventory"]))
    all_sizes = stub.GetStock(pb.StockRequest(product_id="P-1004")).levels
    assert [(lv.size, lv.quantity) for lv in all_sizes] == [("S", 5), ("M", 4), ("L", 0), ("XL", 2)]
    one = stub.GetStock(pb.StockRequest(product_id="P-1004", size="m")).levels
    assert [(lv.size, lv.quantity) for lv in one] == [("M", 4)]
    assert list(stub.GetStock(pb.StockRequest(product_id="P-9999")).levels) == []  # unknown -> empty, not an error
    assert list(stub.GetStock(pb.StockRequest(product_id="P-1004", size="XXL")).levels) == []


# ---- Order ----

def test_order_get_total_and_not_found(grpc_services):
    stub = pb_grpc.OrderServiceStub(_channel(grpc_services["order"]))
    o = stub.GetOrder(pb.GetOrderRequest(order_id="1042"))
    assert o.status == pb.SHIPPED
    assert len(o.items) == 2
    assert o.total == pytest.approx(49.5 + 2 * 60.0)
    with pytest.raises(grpc.RpcError) as e:
        stub.GetOrder(pb.GetOrderRequest(order_id="9999"))
    assert e.value.code() == grpc.StatusCode.NOT_FOUND


def test_track_order_runs_to_delivered_and_persists(grpc_services, tmp_path):
    stub = pb_grpc.OrderServiceStub(_channel(grpc_services["order"]))
    names = [pb.OrderStatus.Name(u.status) for u in stub.TrackOrder(pb.TrackOrderRequest(order_id="1043"))]
    assert names == ["PLACED", "PACKED", "SHIPPED", "OUT_FOR_DELIVERY", "DELIVERED"]
    # every step was written to the service's own DB
    assert stub.GetOrder(pb.GetOrderRequest(order_id="1043")).status == pb.DELIVERED
    row = sqlite3.connect(tmp_path / "orders.db").execute("SELECT status FROM orders WHERE order_id='1043'").fetchone()
    assert row[0] == "DELIVERED"


def test_track_order_mid_flow_delivered_and_cancelled(grpc_services):
    stub = pb_grpc.OrderServiceStub(_channel(grpc_services["order"]))

    def track(oid):
        return [pb.OrderStatus.Name(u.status) for u in stub.TrackOrder(pb.TrackOrderRequest(order_id=oid))]

    assert track("1042") == ["SHIPPED", "OUT_FOR_DELIVERY", "DELIVERED"]
    assert track("1044") == ["DELIVERED"]
    assert track("1045") == ["CANCELLED"]
    with pytest.raises(grpc.RpcError) as e:
        list(stub.TrackOrder(pb.TrackOrderRequest(order_id="9999")))
    assert e.value.code() == grpc.StatusCode.NOT_FOUND


def test_services_seed_only_when_db_empty(tmp_path, seed_dir):
    from services.inventory.db import init_db
    db_path = tmp_path / "inv.db"
    init_db(db_path, seed_dir / "inventory.json")
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE stock SET quantity = 99 WHERE product_id='P-1001'")
    conn.commit()
    init_db(db_path, seed_dir / "inventory.json")  # second start must not re-seed or overwrite
    assert conn.execute("SELECT quantity, COUNT(*) FROM stock WHERE product_id='P-1001'").fetchone() == (99, 1)
    assert conn.execute("SELECT COUNT(*) FROM stock").fetchone()[0] == 8
