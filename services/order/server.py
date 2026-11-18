"""OrderService gRPC server. Run: python -m services.order.server"""
import logging
import os
import threading
from concurrent import futures

import grpc
from dotenv import load_dotenv

from services.common import SEED_DIR, bind_address, env_path, serve
from services.order import db
from shopassist_proto import shopassist_pb2 as pb
from shopassist_proto import shopassist_pb2_grpc as pb_grpc

MESSAGES = {
    "PLACED": "Order placed",
    "PACKED": "Packed at the warehouse",
    "SHIPPED": "Left the warehouse",
    "OUT_FOR_DELIVERY": "Out for delivery with the courier",
    "DELIVERED": "Delivered",
    "CANCELLED": "Order was cancelled",
}


def _update(order_id: str, status: str, timestamp: str) -> pb.OrderStatusUpdate:
    return pb.OrderStatusUpdate(
        order_id=order_id,
        status=pb.OrderStatus.Value(status),
        message=MESSAGES[status],
        timestamp=timestamp,
    )


class OrderServicer(pb_grpc.OrderServiceServicer):
    def __init__(self, db_path, step_seconds: float):
        self.db_path = db_path
        self.step_seconds = step_seconds

    def GetOrder(self, request, context):
        found = db.get_order(self.db_path, request.order_id)
        if found is None:
            context.abort(grpc.StatusCode.NOT_FOUND, f"order {request.order_id} not found")
        order, items = found
        return pb.Order(
            order_id=order["order_id"],
            customer_name=order["customer_name"],
            status=pb.OrderStatus.Value(order["status"]),
            items=[
                pb.OrderItem(product_id=i["product_id"], size=i["size"], quantity=i["quantity"], unit_price=i["unit_price"])
                for i in items
            ],
            total=round(sum(i["quantity"] * i["unit_price"] for i in items), 2),
            updated_at=order["updated_at"],
        )

    def TrackOrder(self, request, context):
        found = db.get_order(self.db_path, request.order_id)
        if found is None:
            context.abort(grpc.StatusCode.NOT_FOUND, f"order {request.order_id} not found")
        order = found[0]
        order_id, status, stamp = order["order_id"], order["status"], order["updated_at"]

        cancelled = threading.Event()  # set when the client goes away, so we stop advancing the order
        context.add_callback(cancelled.set)

        yield _update(order_id, status, stamp)
        while status not in db.TERMINAL:
            if cancelled.wait(self.step_seconds):
                return
            result = db.advance_order(self.db_path, order_id)
            status, stamp = result
            yield _update(order_id, status, stamp)


def create_server(db_path, seed_path, address="127.0.0.1:0", step_seconds=2.0):
    """Build (not start) the server. Returns (server, bound_port); port 0 picks a free one."""
    db.init_db(db_path, seed_path)
    # Each open TrackOrder stream occupies a worker thread while it sleeps.
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=16))
    pb_grpc.add_OrderServiceServicer_to_server(OrderServicer(db_path, step_seconds), server)
    port = server.add_insecure_port(address)
    if port == 0:
        raise RuntimeError(f"could not bind OrderService to {address}")
    return server, port


def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s order %(levelname)s %(message)s")
    address = bind_address("ORDER_ADDR", "localhost:50053")
    server, _ = create_server(
        env_path("ORDER_DB", "data/orders.db"),
        SEED_DIR / "orders.json",
        address,
        float(os.environ.get("ORDER_SIM_STEP_SECONDS") or 2),
    )
    serve(server, "order", address)


if __name__ == "__main__":
    main()
